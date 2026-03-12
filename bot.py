import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import base64
import aiohttp
from datetime import datetime, timedelta
import re

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "times.json"
LINKS_FILE = "links.json"       # 選手ID → チャンネルID の紐付け
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
RECORD_CHANNEL_NAME = "練習ログ"

# ========== データ管理 ==========

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_links():
    if os.path.exists(LINKS_FILE):
        with open(LINKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_links(links):
    with open(LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(links, f, ensure_ascii=False, indent=2)

def parse_time_to_seconds(time_str):
    parts = time_str.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError("フォーマットエラー")

def seconds_to_time(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}:{m:02d}:{s:02d}"
    m = seconds // 60
    s = seconds % 60
    return f"{m}:{s:02d}"

# ========== Claude APIで画像解析 ==========

async def analyze_screenshot(image_bytes: bytes, media_type: str) -> dict | None:
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    prompt = """このランニングアプリのスクリーンショットから情報を抽出してください。
見当たらない場合はnullにし、必ずJSONのみを返してください。

{
  "is_running": true/false（ランニング・ウォーキング等の練習記録か判定）,
  "distance_km": 数値,
  "time": "HH:MM:SS または MM:SS",
  "pace": "MM:SS",
  "avg_heart_rate": 数値または null,
  "max_heart_rate": 数値または null,
  "calories": 数値または null,
  "date": "YYYY/MM/DD または null",
  "app_name": "Garmin/Coros/Nike Run/Strava/Apple Watch等"
}"""

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    body = {
        "model": "claude-opus-4-6",
        "max_tokens": 500,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": prompt}
            ]
        }]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post("https://api.anthropic.com/v1/messages", headers=headers, json=body) as resp:
            if resp.status != 200:
                return None
            result = await resp.json()
            text = result["content"][0]["text"]
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
            return None

# ========== イベント ==========

@bot.event
async def on_ready():
    print(f"✅ {bot.user} 起動！")
    try:
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)}個のコマンド同期完了")
    except Exception as e:
        print(f"❌ 同期エラー: {e}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.name == RECORD_CHANNEL_NAME and message.attachments:
        for att in message.attachments:
            if any(att.filename.lower().endswith(e) for e in [".jpg", ".jpeg", ".png", ".webp"]):
                await process_screenshot(message, att)
                break
    await bot.process_commands(message)

async def process_screenshot(message: discord.Message, attachment: discord.Attachment):
    msg = await message.reply("🔍 練習記録を解析中...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                image_bytes = await resp.read()

        ext = attachment.filename.lower().split(".")[-1]
        mt = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
        result = await analyze_screenshot(image_bytes, mt)

        if not result or not result.get("is_running"):
            await msg.edit(content="❌ 練習記録が読み取れませんでした。GarminやCarosの結果画面のスクショを使ってください。")
            return

        if not result.get("distance_km") or not result.get("time"):
            await msg.edit(content="❌ 距離またはタイムが読み取れませんでした。")
            return

        # タイムを秒に変換
        try:
            seconds = parse_time_to_seconds(result["time"])
        except:
            seconds = 0

        record_date = result.get("date") or datetime.now().strftime("%Y/%m/%d")

        # データ保存
        data = load_data()
        user_id = str(message.author.id)
        user_name = message.author.display_name
        if user_id not in data:
            data[user_id] = {"name": user_name, "records": []}
        data[user_id]["name"] = user_name
        record = {
            "distance": f"{result['distance_km']}km",
            "distance_km": result["distance_km"],
            "time": result["time"],
            "seconds": seconds,
            "pace": result.get("pace"),
            "avg_heart_rate": result.get("avg_heart_rate"),
            "max_heart_rate": result.get("max_heart_rate"),
            "calories": result.get("calories"),
            "date": record_date,
            "source": result.get("app_name", "不明"),
            "memo": ""
        }
        data[user_id]["records"].append(record)
        save_data(data)

        # 練習ログEmbedを作成
        embed = discord.Embed(
            title="🏃 練習記録",
            color=0x00cc66,
            timestamp=datetime.now()
        )
        embed.set_author(name=user_name, icon_url=message.author.display_avatar.url)
        embed.add_field(name="📍 距離", value=f"**{result['distance_km']} km**", inline=True)
        embed.add_field(name="⏱ タイム", value=f"**{result['time']}**", inline=True)
        if result.get("pace"):
            embed.add_field(name="🏃 ペース", value=f"**{result['pace']}/km**", inline=True)
        if result.get("avg_heart_rate"):
            embed.add_field(name="❤️ 平均心拍", value=f"{result['avg_heart_rate']} bpm", inline=True)
        if result.get("max_heart_rate"):
            embed.add_field(name="💓 最大心拍", value=f"{result['max_heart_rate']} bpm", inline=True)
        if result.get("calories"):
            embed.add_field(name="🔥 カロリー", value=f"{result['calories']} kcal", inline=True)
        embed.set_footer(text=f"📱 {result.get('app_name','不明')} | {record_date}")
        embed.set_image(url=attachment.url)  # スクショも添付

        await msg.edit(content="✅ 記録完了！", embed=embed)

        # ========== コーチにDMで転送 ==========
        links = load_links()
        coach_id = links.get(user_id)

        if coach_id:
            coach = message.guild.get_member(int(coach_id))
            if coach:
                try:
                    coach_embed = discord.Embed(
                        title=f"📬 {user_name} から練習報告",
                        color=0xff6600,
                        timestamp=datetime.now()
                    )
                    coach_embed.set_author(name=user_name, icon_url=message.author.display_avatar.url)
                    coach_embed.add_field(name="📍 距離", value=f"**{result['distance_km']} km**", inline=True)
                    coach_embed.add_field(name="⏱ タイム", value=f"**{result['time']}**", inline=True)
                    if result.get("pace"):
                        coach_embed.add_field(name="🏃 ペース", value=f"**{result['pace']}/km**", inline=True)
                    if result.get("avg_heart_rate"):
                        coach_embed.add_field(name="❤️ 平均心拍", value=f"{result['avg_heart_rate']} bpm", inline=True)
                    if result.get("max_heart_rate"):
                        coach_embed.add_field(name="💓 最大心拍", value=f"{result['max_heart_rate']} bpm", inline=True)
                    if result.get("calories"):
                        coach_embed.add_field(name="🔥 カロリー", value=f"{result['calories']} kcal", inline=True)
                    coach_embed.set_footer(text=f"📱 {result.get('app_name','不明')} | {record_date}")
                    coach_embed.set_image(url=attachment.url)

                    await coach.send(
                        content=f"📋 **{user_name} から新しい練習報告が届きました！**",
                        embed=coach_embed
                    )
                except discord.Forbidden:
                    await message.channel.send(
                        f"⚠️ コーチへのDM送信に失敗しました。コーチのDM設定を確認してください。",
                        delete_after=10
                    )
        else:
            await message.reply(
                "⚠️ コーチが未設定です。管理者に `/link` での紐付けを依頼してください。",
                delete_after=10
            )

    except Exception as e:
        await msg.edit(content=f"❌ エラー: {str(e)}")

# ========== 紐付けコマンド（管理者専用） ==========

@bot.tree.command(name="link", description="【管理者】選手とコーチを紐付ける（練習報告がコーチのDMに届く）")
@app_commands.describe(
    member="選手（メンション）",
    coach="担当コーチ（メンション）"
)
@app_commands.checks.has_permissions(manage_channels=True)
async def link(interaction: discord.Interaction, member: discord.Member, coach: discord.Member):
    links = load_links()
    links[str(member.id)] = str(coach.id)
    save_links(links)

    embed = discord.Embed(title="✅ 紐付け完了！", color=0x00cc66)
    embed.add_field(name="選手", value=member.mention, inline=True)
    embed.add_field(name="担当コーチ", value=coach.mention, inline=True)
    embed.set_footer(text="練習ログにスクショを投稿するとコーチのDMに自動で届きます")
    await interaction.response.send_message(embed=embed)

@link.error
async def link_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ このコマンドは管理者のみ使用できます。", ephemeral=True)

@bot.tree.command(name="unlink", description="【管理者】選手の紐付けを解除する")
@app_commands.describe(member="解除する選手（メンション）")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlink(interaction: discord.Interaction, member: discord.Member):
    links = load_links()
    if str(member.id) in links:
        del links[str(member.id)]
        save_links(links)
        await interaction.response.send_message(f"✅ {member.mention} の紐付けを解除しました。")
    else:
        await interaction.response.send_message(f"❌ {member.mention} は紐付けされていません。", ephemeral=True)

@bot.tree.command(name="linklist", description="【管理者】現在の紐付け一覧を表示")
@app_commands.checks.has_permissions(manage_channels=True)
async def linklist(interaction: discord.Interaction):
    links = load_links()
    if not links:
        await interaction.response.send_message("📭 紐付けがまだありません。`/link` で設定してください。", ephemeral=True)
        return

    embed = discord.Embed(title="🔗 選手コーチ紐付け一覧", color=0x3399ff)
    for user_id, coach_id in links.items():
        member = interaction.guild.get_member(int(user_id))
        coach = interaction.guild.get_member(int(coach_id))
        member_str = member.mention if member else f"不明({user_id})"
        coach_str = coach.mention if coach else f"不明({coach_id})"
        embed.add_field(name=member_str, value=f"→ {coach_str}", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="resetlinks", description="【管理者】全ての紐付けをリセットする")
@app_commands.checks.has_permissions(manage_channels=True)
async def resetlinks(interaction: discord.Interaction):
    save_links({})
    await interaction.response.send_message("🗑️ 全ての紐付けをリセットしました。`/link` で再設定してください。", ephemeral=True)
# ========== その他コマンド ==========

@bot.tree.command(name="record", description="タイムを手動で記録する")
@app_commands.describe(distance="距離（例: 5km）", time="タイム（例: 25:30）", pace="ペース（例: 5:06）", avg_heart_rate="平均心拍(bpm)", memo="メモ")
async def record(interaction: discord.Interaction, distance: str, time: str, pace: str = None, avg_heart_rate: int = None, memo: str = ""):
    try:
        seconds = parse_time_to_seconds(time)
    except:
        await interaction.response.send_message("❌ タイム形式エラー。例: `25:30`", ephemeral=True)
        return

    km_match = re.search(r'(\d+\.?\d*)', distance)
    distance_km = float(km_match.group()) if km_match else 0
    data = load_data()
    user_id = str(interaction.user.id)
    if user_id not in data:
        data[user_id] = {"name": interaction.user.display_name, "records": []}
    data[user_id]["name"] = interaction.user.display_name
    data[user_id]["records"].append({
        "distance": distance, "distance_km": distance_km, "time": time, "seconds": seconds,
        "pace": pace, "avg_heart_rate": avg_heart_rate, "max_heart_rate": None,
        "calories": None, "date": datetime.now().strftime("%Y/%m/%d"), "source": "手動", "memo": memo
    })
    save_data(data)

    embed = discord.Embed(title="🏃 記録完了！", color=0x00cc66)
    embed.add_field(name="距離", value=distance, inline=True)
    embed.add_field(name="タイム", value=time, inline=True)
    if pace: embed.add_field(name="ペース", value=f"{pace}/km", inline=True)
    if avg_heart_rate: embed.add_field(name="心拍", value=f"{avg_heart_rate} bpm", inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ranking", description="距離別ベストタイムランキング")
@app_commands.describe(distance="距離（例: 5km, 10km, ハーフ, フル）")
async def ranking(interaction: discord.Interaction, distance: str):
    data = load_data()
    results = []
    for uid, ud in data.items():
        best = None
        for r in ud["records"]:
            if r["distance"].lower() == distance.lower():
                if best is None or r["seconds"] < best["seconds"]:
                    best = r
        if best:
            results.append({"name": ud["name"], **best})

    if not results:
        await interaction.response.send_message(f"❌ `{distance}` の記録なし", ephemeral=True)
        return

    results.sort(key=lambda x: x["seconds"])
    medals = ["🥇", "🥈", "🥉"]
    embed = discord.Embed(title=f"🏆 {distance} ランキング", color=0xffcc00)
    for i, r in enumerate(results[:10]):
        medal = medals[i] if i < 3 else f"{i+1}位"
        pace_str = f" | {r['pace']}/km" if r.get("pace") else ""
        hr_str = f" | ❤️{r['avg_heart_rate']}bpm" if r.get("avg_heart_rate") else ""
        embed.add_field(name=f"{medal} {r['name']}", value=f"**{r['time']}**{pace_str}{hr_str} ({r['date']})", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="stats", description="月間・週間の累積距離と統計")
@app_commands.describe(target="対象メンバー（省略で自分）")
async def stats(interaction: discord.Interaction, target: discord.Member = None):
    user = target or interaction.user
    data = load_data()
    user_id = str(user.id)
    if user_id not in data or not data[user_id]["records"]:
        await interaction.response.send_message("📭 記録なし", ephemeral=True)
        return

    records = data[user_id]["records"]
    today = datetime.now()
    month_str = today.strftime("%Y/%m")
    week_start = today - timedelta(days=today.weekday())

    monthly = [r for r in records if r["date"].startswith(month_str)]
    weekly = [r for r in records if datetime.strptime(r["date"], "%Y/%m/%d") >= week_start]

    def calc(recs):
        if not recs: return None
        total_km = sum(r.get("distance_km", 0) for r in recs)
        total_sec = sum(r.get("seconds", 0) for r in recs)
        hrs = [r["avg_heart_rate"] for r in recs if r.get("avg_heart_rate")]
        return {"count": len(recs), "total_km": round(total_km, 2),
                "total_time": seconds_to_time(total_sec), "avg_hr": int(sum(hrs)/len(hrs)) if hrs else None}

    w, m = calc(weekly), calc(monthly)
    embed = discord.Embed(title=f"📊 {user.display_name} の練習統計", color=0x3399ff)
    week_end = week_start + timedelta(days=6)
    if w:
        hr_str = f" ｜ ❤️ {w['avg_hr']} bpm" if w['avg_hr'] else ""
        embed.add_field(name=f"📅 今週 ({week_start.strftime('%m/%d')}〜{week_end.strftime('%m/%d')})",
                        value=f"🏃 **{w['total_km']} km** ｜ {w['count']}回 ｜ {w['total_time']}{hr_str}", inline=False)
    if m:
        hr_str = f" ｜ ❤️ {m['avg_hr']} bpm" if m['avg_hr'] else ""
        embed.add_field(name=f"📆 今月 ({today.strftime('%Y年%m月')})",
                        value=f"🏃 **{m['total_km']} km** ｜ {m['count']}回 ｜ {m['total_time']}{hr_str}", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="myrecords", description="自分の直近記録一覧")
async def myrecords(interaction: discord.Interaction):
    data = load_data()
    user_id = str(interaction.user.id)
    if user_id not in data or not data[user_id]["records"]:
        await interaction.response.send_message("📭 記録なし", ephemeral=True)
        return
    records = sorted(data[user_id]["records"], key=lambda x: x["date"], reverse=True)[:10]
    embed = discord.Embed(title=f"📋 {interaction.user.display_name} 直近10件", color=0x3399ff)
    for r in records:
        pace_str = f" {r['pace']}/km" if r.get("pace") else ""
        hr_str = f" ❤️{r['avg_heart_rate']}bpm" if r.get("avg_heart_rate") else ""
        embed.add_field(name=f"{r['date']} — {r['distance']}",
                        value=f"⏱ **{r['time']}**{pace_str}{hr_str} 📱{r.get('source','手動')}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.run(os.environ["DISCORD_TOKEN"])
