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
LINKS_FILE = "links.json"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

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
  "is_running": true/false,
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

# ========== 体調ボタン ==========

class ConditionView(discord.ui.View):
    def __init__(self, user_id: str, user_name: str, coach_id: str, result: dict, record_date: str):
        super().__init__(timeout=3600)  # 1時間で無効化
        self.user_id = user_id
        self.user_name = user_name
        self.coach_id = coach_id
        self.result = result
        self.record_date = record_date
        self.responded = False

    async def send_to_coach(self, interaction: discord.Interaction, condition: str, color: int):
        if self.responded:
            await interaction.response.send_message("⚠️ すでに体調を送信済みです。", ephemeral=True)
            return
        self.responded = True

        # ボタンを無効化
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        condition_emoji = {"いい": "😊", "ふつう": "😐", "わるい": "😞"}.get(condition, "")

        await interaction.response.send_message(
            f"{condition_emoji} 体調「**{condition}**」を送信しました！",
            ephemeral=True
        )

        # 「わるい」の場合のみコーチにDM
        if condition == "わるい":
            coach = interaction.guild.get_member(int(self.coach_id))
            if coach:
                try:
                    embed = discord.Embed(
                        title=f"🚨 {self.user_name} の体調が「わるい」です",
                        color=0xff0000,
                        timestamp=datetime.now()
                    )
                    embed.set_author(name=self.user_name)
                    embed.add_field(name="📍 距離", value=f"**{self.result.get('distance_km')} km**", inline=True)
                    embed.add_field(name="⏱ タイム", value=f"**{self.result.get('time')}**", inline=True)
                    if self.result.get("pace"):
                        embed.add_field(name="🏃 ペース", value=f"**{self.result.get('pace')}/km**", inline=True)
                    if self.result.get("avg_heart_rate"):
                        embed.add_field(name="❤️ 平均心拍", value=f"{self.result.get('avg_heart_rate')} bpm", inline=True)
                    embed.add_field(name="😞 体調", value="**わるい**", inline=True)
                    embed.set_footer(text=f"📱 {self.result.get('app_name','不明')} | {self.record_date}")
                    await coach.send(
                        content=f"⚠️ **{self.user_name} から体調不良の報告が届きました！**",
                        embed=embed
                    )
                except discord.Forbidden:
                    pass

    @discord.ui.button(label="😊 いい", style=discord.ButtonStyle.success)
    async def good(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.user_id):
            await interaction.response.send_message("❌ あなたは押せません。", ephemeral=True)
            return
        await self.send_to_coach(interaction, "いい", 0x00cc66)

    @discord.ui.button(label="😐 ふつう", style=discord.ButtonStyle.secondary)
    async def normal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.user_id):
            await interaction.response.send_message("❌ あなたは押せません。", ephemeral=True)
            return
        await self.send_to_coach(interaction, "ふつう", 0xffcc00)

    @discord.ui.button(label="😞 わるい", style=discord.ButtonStyle.danger)
    async def bad(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.user_id):
            await interaction.response.send_message("❌ あなたは押せません。", ephemeral=True)
            return
        await self.send_to_coach(interaction, "わるい", 0xff0000)

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

    # 選手の個人チャンネルへの画像投稿を検知
    links = load_links()
    channel_map = {v.get("channel_id"): k for k, v in links.items() if v.get("channel_id")}

    if str(message.channel.id) in channel_map and message.attachments:
        for att in message.attachments:
            if any(att.filename.lower().endswith(e) for e in [".jpg", ".jpeg", ".png", ".webp"]):
                user_id = channel_map[str(message.channel.id)]
                coach_id = links[user_id].get("coach_id")
                await process_screenshot(message, att, user_id, coach_id)
                break

    await bot.process_commands(message)

async def process_screenshot(message: discord.Message, attachment: discord.Attachment, user_id: str, coach_id: str):
    msg = await message.reply("🔍 練習記録を解析中...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                image_bytes = await resp.read()

        ext = attachment.filename.lower().split(".")[-1]
        mt = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
        result = await analyze_screenshot(image_bytes, mt)

        if not result or not result.get("is_running") or not result.get("distance_km"):
            await msg.edit(content="❌ 練習記録が読み取れませんでした。Garmin・Caros等の結果画面を使ってください。")
            return

        try:
            seconds = parse_time_to_seconds(result["time"])
        except:
            seconds = 0

        record_date = result.get("date") or datetime.now().strftime("%Y/%m/%d")
        user_name = message.author.display_name

        # データ保存
        data = load_data()
        if user_id not in data:
            data[user_id] = {"name": user_name, "records": []}
        data[user_id]["name"] = user_name
        data[user_id]["records"].append({
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
        })
        save_data(data)

        # 練習記録Embed
        embed = discord.Embed(title="✅ 練習記録完了！", color=0x00cc66, timestamp=datetime.now())
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

        # 体調ボタン付きで送信
        view = ConditionView(user_id, user_name, coach_id, result, record_date)
        await msg.edit(content="今日の体調を教えてください👇", embed=embed, view=view)

    except Exception as e:
        await msg.edit(content=f"❌ エラー: {str(e)}")

# ========== 個人チャンネル作成 ==========

@bot.tree.command(name="createroom", description="【管理者】選手の個人練習チャンネルを作成する")
@app_commands.describe(
    member="選手（メンション）",
    coach="担当コーチ（メンション）"
)
@app_commands.checks.has_permissions(manage_channels=True)
async def createroom(interaction: discord.Interaction, member: discord.Member, coach: discord.Member):
    await interaction.response.defer()
    guild = interaction.guild

    # カテゴリを探す or 作成
    category = discord.utils.get(guild.categories, name="練習ログ")
    if not category:
        category = await guild.create_category("練習ログ")

    # チャンネル名
    channel_name = f"📋{member.display_name}"

    # 既存チェック
    existing = discord.utils.get(guild.text_channels, name=channel_name.lower().replace(" ", "-"))
    if existing:
        await interaction.followup.send(f"⚠️ {member.mention} のチャンネルは既に存在します: {existing.mention}")
        return

    # 権限設定（選手とBotのみ）
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, embed_links=True, attach_files=True)
    }

    channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic=f"{member.display_name} の練習ログチャンネル"
    )

    # 紐付け保存
    links = load_links()
    links[str(member.id)] = {
        "coach_id": str(coach.id),
        "channel_id": str(channel.id)
    }
    save_links(links)

    embed = discord.Embed(title="✅ 個人チャンネル作成完了！", color=0x00cc66)
    embed.add_field(name="選手", value=member.mention, inline=True)
    embed.add_field(name="担当コーチ", value=coach.mention, inline=True)
    embed.add_field(name="チャンネル", value=channel.mention, inline=False)
    embed.set_footer(text="スクショを投稿すると体調ボタンが表示されます")
    await interaction.followup.send(embed=embed)

    # チャンネルに案内メッセージ
    await channel.send(
        f"👋 {member.mention} の練習ログチャンネルへようこそ！\n"
        f"GarminやCarosの練習結果スクショをここに投稿してください📸\n"
        f"自動で記録され、体調確認ボタンが表示されます。"
    )

@bot.tree.command(name="link", description="【管理者】既存チャンネルと選手・コーチを紐付ける")
@app_commands.describe(member="選手", coach="担当コーチ", channel="チャンネル")
@app_commands.checks.has_permissions(manage_channels=True)
async def link(interaction: discord.Interaction, member: discord.Member, coach: discord.Member, channel: discord.TextChannel):
    links = load_links()
    links[str(member.id)] = {"coach_id": str(coach.id), "channel_id": str(channel.id)}
    save_links(links)
    embed = discord.Embed(title="✅ 紐付け完了！", color=0x00cc66)
    embed.add_field(name="選手", value=member.mention, inline=True)
    embed.add_field(name="コーチ", value=coach.mention, inline=True)
    embed.add_field(name="チャンネル", value=channel.mention, inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="linklist", description="【管理者】現在の紐付け一覧")
@app_commands.checks.has_permissions(manage_channels=True)
async def linklist(interaction: discord.Interaction):
    links = load_links()
    if not links:
        await interaction.response.send_message("📭 紐付けなし", ephemeral=True)
        return
    embed = discord.Embed(title="🔗 選手コーチ紐付け一覧", color=0x3399ff)
    for user_id, info in links.items():
        member = interaction.guild.get_member(int(user_id))
        coach = interaction.guild.get_member(int(info.get("coach_id", 0)))
        channel = interaction.guild.get_channel(int(info.get("channel_id", 0)))
        member_str = member.mention if member else f"不明({user_id})"
        coach_str = coach.mention if coach else "未設定"
        channel_str = channel.mention if channel else "未設定"
        embed.add_field(name=member_str, value=f"コーチ: {coach_str}\nチャンネル: {channel_str}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="resetlinks", description="【管理者】全ての紐付けをリセット")
@app_commands.checks.has_permissions(manage_channels=True)
async def resetlinks(interaction: discord.Interaction):
    save_links({})
    await interaction.response.send_message("🗑️ 全リセット完了。`/createroom` で再設定してください。", ephemeral=True)

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

# ========== Interval.icu 連携 ==========

ICU_FILE = "icu_settings.json"
SCHEDULE_FILE = "icu_schedule.json"

def load_icu():
    if os.path.exists(ICU_FILE):
        with open(ICU_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_icu(data):
    with open(ICU_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_schedule():
    if os.path.exists(SCHEDULE_FILE):
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_schedule(data):
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

async def fetch_icu_activities(api_key: str, athlete_id: str, date: str = None) -> list:
    """Interval.icuから練習データを取得"""
    if not date:
        date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    url = f"https://intervals.icu/api/v1/athlete/{athlete_id}/activities"
    params = {"oldest": date, "newest": date}
    auth = aiohttp.BasicAuth("API_KEY", api_key)

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, auth=auth) as resp:
            if resp.status != 200:
                return []
            return await resp.json()

async def fetch_icu_activity_detail(api_key: str, athlete_id: str, activity_id: str) -> dict:
    """活動の詳細（ゾーン・負荷）を取得"""
    url = f"https://intervals.icu/api/v1/athlete/{athlete_id}/activities/{activity_id}"
    auth = aiohttp.BasicAuth("API_KEY", api_key)

    async with aiohttp.ClientSession() as session:
        async with session.get(url, auth=auth) as resp:
            if resp.status != 200:
                return {}
            return await resp.json()

def format_icu_embed(activity: dict, detail: dict, athlete_name: str) -> discord.Embed:
    """Interval.icuデータをEmbedに整形"""
    name = activity.get("name", "練習")
    date = activity.get("start_date_local", "")[:10]

    embed = discord.Embed(
        title=f"📊 {athlete_name} の練習データ",
        description=f"**{name}** — {date}",
        color=0x4361ee,
        timestamp=datetime.now()
    )

    # 基本データ
    distance = activity.get("distance", 0)
    if distance:
        embed.add_field(name="📍 距離", value=f"**{distance/1000:.2f} km**", inline=True)

    moving_time = activity.get("moving_time", 0)
    if moving_time:
        h, m = divmod(moving_time // 60, 60)
        s = moving_time % 60
        time_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        embed.add_field(name="⏱ タイム", value=f"**{time_str}**", inline=True)

    avg_speed = activity.get("average_speed", 0)
    if avg_speed and distance:
        pace_sec = 1000 / avg_speed
        pace_str = f"{int(pace_sec//60)}:{int(pace_sec%60):02d}"
        embed.add_field(name="🏃 ペース", value=f"**{pace_str}/km**", inline=True)

    # 心拍数
    avg_hr = activity.get("average_heartrate")
    max_hr = activity.get("max_heartrate")
    if avg_hr:
        embed.add_field(name="❤️ 平均心拍", value=f"{int(avg_hr)} bpm", inline=True)
    if max_hr:
        embed.add_field(name="💓 最大心拍", value=f"{int(max_hr)} bpm", inline=True)

    # 練習負荷
    load = detail.get("training_load") or activity.get("training_load")
    if load:
        embed.add_field(name="💪 練習負荷", value=f"**{int(load)}**", inline=True)

    # ペースゾーン（滞在時間）
    pace_zones = detail.get("pace_zones") or []
    if pace_zones:
        zone_lines = []
        for z in pace_zones[:5]:
            zone_name = z.get("name", "")
            secs = z.get("time", 0)
            if secs > 0:
                m, s = divmod(secs, 60)
                zone_lines.append(f"{zone_name}: {m}分{s:02d}秒")
        if zone_lines:
            embed.add_field(name="⏱ ペース別滞在時間", value="\n".join(zone_lines), inline=False)

    # 心拍ゾーン
    hr_zones = detail.get("hr_zones") or []
    if hr_zones:
        zone_lines = []
        for z in hr_zones[:5]:
            zone_name = z.get("name", "")
            secs = z.get("time", 0)
            if secs > 0:
                m, s = divmod(secs, 60)
                zone_lines.append(f"{zone_name}: {m}分{s:02d}秒")
        if zone_lines:
            embed.add_field(name="❤️ 心拍ゾーン別滞在時間", value="\n".join(zone_lines), inline=False)

    embed.set_footer(text="📱 Interval.icu")
    return embed

async def send_icu_report(bot_instance, coach_id: str, api_key: str, athletes: dict, date: str = None):
    """コーチに全選手のレポートを送信"""
    coach = bot_instance.get_user(int(coach_id))
    if not coach:
        return

    if not date:
        date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    sent = 0
    for athlete_name, athlete_id in athletes.items():
        activities = await fetch_icu_activities(api_key, athlete_id, date)
        if not activities:
            continue
        for act in activities[:1]:  # 最新1件
            detail = await fetch_icu_activity_detail(api_key, athlete_id, act.get("id", ""))
            embed = format_icu_embed(act, detail, athlete_name)
            try:
                await coach.send(embed=embed)
                sent += 1
            except:
                pass

    if sent == 0:
        try:
            await coach.send(f"📭 {date} の練習データはありませんでした。")
        except:
            pass

# ========== 定時送信タスク ==========

from discord.ext import tasks

@tasks.loop(minutes=1)
async def icu_scheduler():
    """毎分チェックして設定時刻に送信"""
    now = datetime.now().strftime("%H:%M")
    schedule = load_schedule()
    icu = load_icu()

    for coach_id, time_str in schedule.items():
        if time_str == now:
            athletes = icu.get(coach_id, {}).get("athletes", {})
            api_key = icu.get(coach_id, {}).get("api_key", "")
            if api_key and athletes:
                await send_icu_report(bot, coach_id, api_key, athletes)

@bot.listen("on_ready")
async def start_scheduler():
    if not icu_scheduler.is_running():
        icu_scheduler.start()

# ========== Interval.icu コマンド ==========

@bot.tree.command(name="icu_setup", description="【コーチ】Interval.icu APIキーと選手を登録する")
@app_commands.describe(
    api_key="Interval.icu の APIキー",
    athlete_name="選手の名前",
    athlete_id="選手のInterval.icuアスリートID"
)
async def icu_setup(interaction: discord.Interaction, api_key: str, athlete_name: str, athlete_id: str):
    icu = load_icu()
    coach_id = str(interaction.user.id)

    if coach_id not in icu:
        icu[coach_id] = {"api_key": api_key, "athletes": {}}

    icu[coach_id]["api_key"] = api_key
    icu[coach_id]["athletes"][athlete_name] = athlete_id
    save_icu(icu)

    embed = discord.Embed(title="✅ Interval.icu 設定完了！", color=0x00cc66)
    embed.add_field(name="選手", value=athlete_name, inline=True)
    embed.add_field(name="アスリートID", value=athlete_id, inline=True)
    embed.set_footer(text="/icu_setup を繰り返して選手を追加できます")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="icu", description="選手のInterval.icu練習データを取得")
@app_commands.describe(
    athlete_name="選手の名前",
    date="日付（例: 2026-03-14）省略すると昨日"
)
async def icu(interaction: discord.Interaction, athlete_name: str, date: str = None):
    await interaction.response.defer()
    icu_data = load_icu()
    coach_id = str(interaction.user.id)

    if coach_id not in icu_data:
        await interaction.followup.send("❌ 先に `/icu_setup` でAPIキーを設定してください。", ephemeral=True)
        return

    api_key = icu_data[coach_id]["api_key"]
    athletes = icu_data[coach_id]["athletes"]

    if athlete_name not in athletes:
        names = "、".join(athletes.keys())
        await interaction.followup.send(f"❌ 選手が見つかりません。登録済み: {names}", ephemeral=True)
        return

    athlete_id = athletes[athlete_name]
    target_date = date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    activities = await fetch_icu_activities(api_key, athlete_id, target_date)
    if not activities:
        await interaction.followup.send(f"📭 {athlete_name} の {target_date} の練習データはありません。")
        return

    for act in activities[:1]:
        detail = await fetch_icu_activity_detail(api_key, athlete_id, act.get("id", ""))
        embed = format_icu_embed(act, detail, athlete_name)
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="icu_settime", description="【コーチ】Interval.icuレポートの自動送信時刻を設定")
@app_commands.describe(time="送信時刻（例: 09:00）")
async def icu_settime(interaction: discord.Interaction, time: str):
    # 時刻フォーマット確認
    try:
        datetime.strptime(time, "%H:%M")
    except:
        await interaction.response.send_message("❌ 時刻の形式が違います。例: `09:00`", ephemeral=True)
        return

    schedule = load_schedule()
    schedule[str(interaction.user.id)] = time
    save_schedule(schedule)

    embed = discord.Embed(title="✅ 自動送信時刻を設定しました！", color=0x00cc66)
    embed.add_field(name="送信時刻", value=f"毎日 **{time}**", inline=True)
    embed.set_footer(text="前日の全選手の練習データがDMに届きます")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="icu_canceltime", description="【コーチ】自動送信をキャンセル")
async def icu_canceltime(interaction: discord.Interaction):
    schedule = load_schedule()
    coach_id = str(interaction.user.id)
    if coach_id in schedule:
        del schedule[coach_id]
        save_schedule(schedule)
        await interaction.response.send_message("✅ 自動送信をキャンセルしました。", ephemeral=True)
    else:
        await interaction.response.send_message("❌ 自動送信は設定されていません。", ephemeral=True)

@bot.tree.command(name="icu_athletes", description="【コーチ】登録済み選手一覧を表示")
async def icu_athletes(interaction: discord.Interaction):
    icu_data = load_icu()
    coach_id = str(interaction.user.id)

    if coach_id not in icu_data or not icu_data[coach_id].get("athletes"):
        await interaction.response.send_message("📭 選手が登録されていません。`/icu_setup` で登録してください。", ephemeral=True)
        return

    athletes = icu_data[coach_id]["athletes"]
    schedule = load_schedule()
    time_str = schedule.get(coach_id, "未設定")

    embed = discord.Embed(title="👥 登録済み選手一覧", color=0x3399ff)
    for name, aid in athletes.items():
        embed.add_field(name=name, value=f"ID: {aid}", inline=True)
    embed.set_footer(text=f"自動送信時刻: {time_str}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ========== 起動 ==========
bot.run(os.environ["DISCORD_TOKEN"])
