import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import base64
import aiohttp
from datetime import datetime, timedelta, timezone
import re

JST = timezone(timedelta(hours=9))

def now_jst() -> datetime:
    """日本時間の現在時刻を返す"""
    return datetime.now(JST)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "times.json"
LINKS_FILE = "links.json"
PUBLIC_CHANNELS_FILE = "public_channels.json"
AGREE_FILE = "agreed_members.json"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

TERMS_TEXT = """**PROJECT NN 利用規約**

**第1条（目的・理念）**
PROJECT NNは科学的根拠に基づいたトレーニングで競技力向上を目指す非体育会系コーチングチームです。

**第2条（行動規範）**
・メンバー同士を尊重し、誹謗中傷・ハラスメントを行わないこと
・論理的・建設的なコミュニケーションを心がけること
・差別的発言・スパム・他メンバーの個人情報の無断公開を禁止します

**第3条（練習データ・個人情報）**
・収集データ：練習記録（距離・タイム・ペース・心拍数等）、Interval.icuの活動データ、DiscordユーザーID
・利用目的：練習管理・コーチへのレポート送信・疲労検知・AIコメント生成のみ
・第三者への無断提供は行いません
・退会時のデータ削除は管理者までご連絡ください

**第4条（Botの利用）**
・Botは正当な目的にのみ使用すること
・AIコメント・疲労分析は参考情報であり医学的診断ではありません

**第5条（コーチング）**
・コーチのアドバイスは科学的根拠に基づきますが個人差があります
・怪我・体調不良時はコーチに速やかに報告してください

**第6条（退会・除名）**
・退会はいつでも自由です
・規約違反・他メンバーへの迷惑行為・3ヶ月以上の無連絡の場合、除名することがあります

**第7条（免責事項）**
・本サービス利用による怪我・健康被害・データ消失について責任を負いません
・AI機能の完全な正確性を保証するものではありません

**第8条（規約の変更）**
・規約変更時はサーバー内でアナウンスします

━━━━━━━━━━━━━━━━━
*PROJECT NN — 宇宙一アツい、非体育会系チーム*"""

def load_agreed():
    if os.path.exists(AGREE_FILE):
        with open(AGREE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_agreed(data):
    with open(AGREE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def has_agreed(user_id: str, guild_id: str) -> bool:
    agreed = load_agreed()
    return user_id in agreed.get(guild_id, {})

def mark_agreed(user_id: str, guild_id: str, user_name: str):
    agreed = load_agreed()
    if guild_id not in agreed:
        agreed[guild_id] = {}
    agreed[guild_id][user_id] = {
        "name": user_name,
        "agreed_at": now_jst().strftime("%Y/%m/%d %H:%M")
    }
    save_agreed(agreed)

def load_public_channels():
    if os.path.exists(PUBLIC_CHANNELS_FILE):
        with open(PUBLIC_CHANNELS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_public_channels(data):
    with open(PUBLIC_CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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
                        timestamp=now_jst()
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
    # 全サーバーの権限を自動設定
    for guild in bot.guilds:
        await setup_guild_permissions(guild)
        print(f"✅ {guild.name} の権限設定完了")

@bot.tree.command(name="setup_permissions", description="【管理者】サーバーの権限を自動設定する（選手ロール・チャンネル権限）")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_permissions(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await setup_guild_permissions(interaction.guild)

    member_role = discord.utils.get(interaction.guild.roles, name=MEMBER_ROLE_NAME)
    terms_ch = discord.utils.get(interaction.guild.channels, name=TERMS_CHANNEL_NAME)

    embed = discord.Embed(title="✅ 権限設定完了！", color=0x00cc66, timestamp=now_jst())
    embed.add_field(name="選手ロール", value=member_role.mention if member_role else "作成済み", inline=True)
    embed.add_field(name="規約チャンネル", value=terms_ch.mention if terms_ch else f"「{TERMS_CHANNEL_NAME}」が見つかりません", inline=True)
    embed.add_field(
        name="設定内容",
        value="・@everyone → 全チャンネル閲覧不可\n・📜利用条約同意 → 全員閲覧可\n・選手ロール → 全チャンネル解放",
        inline=False
    )
    embed.set_footer(text="新メンバーは同意後に自動で選手ロールが付与されます")
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.event
async def on_member_join(member: discord.Member):
    """新メンバー参加時: 未同意ロール付与 → ウェルカム → 規約送信"""
    guild    = member.guild
    guild_id = str(guild.id)

    # ── 未同意ロールを付与（なければ作成） ──
    pending_role = discord.utils.get(guild.roles, name=PENDING_ROLE_NAME)
    if not pending_role:
        try:
            pending_role = await guild.create_role(name=PENDING_ROLE_NAME, reason="利用規約未同意")
        except Exception:
            pending_role = None
    if pending_role:
        try:
            await member.add_roles(pending_role, reason="利用規約未同意")
        except Exception:
            pass

    # ── 公開チャンネルにウェルカム → 規約 ──
    pub = load_public_channels()
    channel_ids = pub.get(guild_id, [])

    for ch_id in channel_ids:
        channel = guild.get_channel(int(ch_id))
        if not channel:
            continue
        try:
            # ① ウェルカムメッセージ
            await channel.send(
                f"🎉 **{member.mention} さん、PROJECT NN へようこそ！**\n\n"
                f"宇宙一アツい、非体育会系チームへの参加、ありがとうございます。\n"
                f"熱量はどこにも負けないのに、ガチガチの上下関係もない。\n"
                f"論理的に、自分の頭で考えて走る。\n"
                f"それがPROJECT NNのスタイルです。\n\n"
                f"一緒に強くなりましょう！💪"
            )

            # ② 利用規約 ＋ 同意ボタン
            embed = discord.Embed(
                title="📋 PROJECT NN 利用規約",
                description=TERMS_TEXT,
                color=0x4361ee,
                timestamp=now_jst()
            )
            embed.set_footer(text="✅ 同意するボタンを押すと全チャンネルが解放されます")
            view = TermsView(str(member.id), guild_id, member.display_name)
            await channel.send(
                content=f"{member.mention} **利用規約をご確認の上、同意をお願いします。同意するまでこのチャンネルのみ閲覧できます。**",
                embed=embed,
                view=view
            )
        except Exception:
            pass

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
        except (ValueError, TypeError):
            seconds = 0

        record_date = result.get("date") or now_jst().strftime("%Y/%m/%d")
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
        embed = discord.Embed(title="✅ 練習記録完了！", color=0x00cc66, timestamp=now_jst())
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

# ========== 公開チャンネル ==========

@bot.tree.command(name="createpublic", description="【管理者】全メンバーが見られる公開チャンネルを作成する")
@app_commands.describe(
    channel_name="チャンネル名",
    category_name="カテゴリ名（省略可）"
)
@app_commands.checks.has_permissions(manage_channels=True)
async def createpublic(interaction: discord.Interaction, channel_name: str, category_name: str = None):
    await interaction.response.defer()
    guild = interaction.guild

    # カテゴリ
    category = None
    if category_name:
        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            category = await guild.create_category(category_name)

    # @everyone が読み書きできる権限
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, embed_links=True)
    }

    channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites
    )

    # 公開チャンネルとして登録
    pub = load_public_channels()
    guild_id = str(guild.id)
    if guild_id not in pub:
        pub[guild_id] = []
    if str(channel.id) not in pub[guild_id]:
        pub[guild_id].append(str(channel.id))
    save_public_channels(pub)

    # 既存メンバー全員に権限付与
    count = 0
    for member in guild.members:
        if member.bot:
            continue
        try:
            await channel.set_permissions(member, read_messages=True, send_messages=True)
            count += 1
        except Exception:
            pass

    embed = discord.Embed(title="✅ 公開チャンネル作成完了！", color=0x00cc66)
    embed.add_field(name="チャンネル", value=channel.mention, inline=True)
    embed.add_field(name="権限付与", value=f"{count}名", inline=True)
    embed.set_footer(text="新メンバーが参加すると自動で閲覧権限が付与されます")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="setpublic", description="【管理者】既存チャンネルを新メンバー自動参加チャンネルに設定する")
@app_commands.describe(channel="対象チャンネル")
@app_commands.checks.has_permissions(manage_channels=True)
async def setpublic(interaction: discord.Interaction, channel: discord.TextChannel):
    pub = load_public_channels()
    guild_id = str(interaction.guild.id)
    if guild_id not in pub:
        pub[guild_id] = []
    if str(channel.id) not in pub[guild_id]:
        pub[guild_id].append(str(channel.id))
        save_public_channels(pub)
        await interaction.response.send_message(
            f"✅ {channel.mention} を新メンバー自動参加チャンネルに設定しました。", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"⚠️ {channel.mention} はすでに設定済みです。", ephemeral=True)

@bot.tree.command(name="unsetpublic", description="【管理者】新メンバー自動参加チャンネルの設定を解除する")
@app_commands.describe(channel="対象チャンネル")
@app_commands.checks.has_permissions(manage_channels=True)
async def unsetpublic(interaction: discord.Interaction, channel: discord.TextChannel):
    pub = load_public_channels()
    guild_id = str(interaction.guild.id)
    ch_id = str(channel.id)
    if ch_id in pub.get(guild_id, []):
        pub[guild_id].remove(ch_id)
        save_public_channels(pub)
        await interaction.response.send_message(
            f"✅ {channel.mention} の自動参加設定を解除しました。", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"❌ {channel.mention} は設定されていません。", ephemeral=True)

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

# ========== 利用規約 ==========

PENDING_ROLE_NAME = "未同意"
MEMBER_ROLE_NAME  = "選手"
TERMS_CHANNEL_NAME = "📜利用条約同意"

async def setup_guild_permissions(guild: discord.Guild):
    """
    サーバーの権限を自動設定:
    - @everyone: 全チャンネル閲覧・送信OFF
    - 📜利用条約同意チャンネル: @everyoneに閲覧・送信ON
    - 選手ロール: 全チャンネル閲覧・送信ON（📜利用条約同意以外）
    """
    # 選手ロールを取得or作成
    member_role = discord.utils.get(guild.roles, name=MEMBER_ROLE_NAME)
    if not member_role:
        try:
            member_role = await guild.create_role(
                name=MEMBER_ROLE_NAME,
                color=discord.Color.blue(),
                reason="PROJECT NN 選手ロール自動作成"
            )
        except Exception:
            return

    # @everyoneのデフォルト権限を最小化
    try:
        await guild.default_role.edit(
            permissions=discord.Permissions(
                read_messages=False,
                send_messages=False,
                read_message_history=False
            ),
            reason="利用規約同意システム: @everyone権限を制限"
        )
    except Exception:
        pass

    # 全チャンネルの権限設定
    for channel in guild.channels:
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel)):
            continue
        try:
            if channel.name == TERMS_CHANNEL_NAME or (
                isinstance(channel, discord.CategoryChannel) and channel.name == TERMS_CHANNEL_NAME
            ):
                # 📜利用条約同意チャンネル: @everyoneに閲覧・送信ON
                await channel.set_permissions(guild.default_role,
                    read_messages=True, send_messages=False,
                    read_message_history=True)
                await channel.set_permissions(member_role,
                    read_messages=True, send_messages=True)
            else:
                # その他のチャンネル: @everyoneはOFF、選手ロールはON
                await channel.set_permissions(guild.default_role,
                    read_messages=False, send_messages=False)
                await channel.set_permissions(member_role,
                    read_messages=True, send_messages=True)
        except Exception:
            pass


async def apply_agreed_roles(member: discord.Member):
    """同意後: 未同意ロール削除 → 選手ロール付与"""
    guild = member.guild

    # 未同意ロール削除
    pending_role = discord.utils.get(guild.roles, name=PENDING_ROLE_NAME)
    if pending_role and pending_role in member.roles:
        try:
            await member.remove_roles(pending_role, reason="利用規約同意")
        except Exception:
            pass

    # 選手ロール付与（なければ作成）
    member_role = discord.utils.get(guild.roles, name=MEMBER_ROLE_NAME)
    if not member_role:
        try:
            member_role = await guild.create_role(name=MEMBER_ROLE_NAME, reason="利用規約同意後付与")
        except Exception:
            return
    try:
        await member.add_roles(member_role, reason="利用規約同意")
    except Exception:
        pass

class TermsView(discord.ui.View):
    def __init__(self, user_id: str, guild_id: str, user_name: str):
        super().__init__(timeout=None)  # タイムアウトなし（既存メンバーも使えるよう）
        self.user_id   = user_id
        self.guild_id  = guild_id
        self.user_name = user_name

    @discord.ui.button(label="✅ 同意する", style=discord.ButtonStyle.success, custom_id="terms_agree")
    async def agree_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("❌ あなたは押せません。", ephemeral=True)
            return
        mark_agreed(self.user_id, self.guild_id, self.user_name)

        # ロール切り替え
        await apply_agreed_roles(interaction.user)

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            "✅ **利用規約に同意しました。PROJECT NN へようこそ！**\n一緒に強くなりましょう！💪",
            ephemeral=True
        )

    @discord.ui.button(label="❌ 同意しない", style=discord.ButtonStyle.danger, custom_id="terms_disagree")
    async def disagree_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("❌ あなたは押せません。", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            "規約に同意されなかったため、登録はキャンセルされました。\n"
            "参加を希望する場合は管理者までお問い合わせください。",
            ephemeral=True
        )

@bot.tree.command(name="terms", description="PROJECT NN 利用規約を表示する")
async def terms(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 PROJECT NN 利用規約",
        description=TERMS_TEXT,
        color=0x4361ee,
        timestamp=now_jst()
    )
    embed.set_footer(text="同意ボタンを押すと規約に同意したことになります")
    user_id  = str(interaction.user.id)
    guild_id = str(interaction.guild_id)
    if has_agreed(user_id, guild_id):
        embed.add_field(name="✅ 同意済み", value=f"{interaction.user.display_name} さんは同意済みです", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        view = TermsView(user_id, guild_id, interaction.user.display_name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="terms_send", description="【管理者】未同意の既存メンバーに規約同意フォームを送信する")
@app_commands.checks.has_permissions(manage_guild=True)
async def terms_send(interaction: discord.Interaction):
    """未同意の既存メンバー全員に未同意ロールを付与し、規約チャンネルに規約を投稿する"""
    await interaction.response.defer(ephemeral=True)
    guild    = interaction.guild
    guild_id = str(guild.id)
    agreed   = load_agreed().get(guild_id, {})

    # 未同意ロール取得 or 作成
    pending_role = discord.utils.get(guild.roles, name=PENDING_ROLE_NAME)
    if not pending_role:
        try:
            pending_role = await guild.create_role(name=PENDING_ROLE_NAME, reason="利用規約未同意")
        except Exception:
            pending_role = None

    # 未同意メンバーに未同意ロール付与
    unagreed = [m for m in guild.members if not m.bot and str(m.id) not in agreed]
    role_count = 0
    for member in unagreed:
        if pending_role and pending_role not in member.roles:
            try:
                await member.add_roles(pending_role, reason="利用規約未同意（既存メンバー）")
                role_count += 1
            except Exception:
                pass

    # 📜利用条約同意チャンネルに規約+ボタンを投稿
    terms_ch = discord.utils.get(guild.text_channels, name=TERMS_CHANNEL_NAME)
    if not terms_ch:
        await interaction.followup.send(
            f"❌ 「{TERMS_CHANNEL_NAME}」チャンネルが見つかりません。", ephemeral=True)
        return

    mentions = " ".join(m.mention for m in unagreed[:20])
    embed = discord.Embed(
        title="📋 PROJECT NN 利用規約",
        description=TERMS_TEXT,
        color=0x4361ee,
        timestamp=now_jst()
    )
    embed.set_footer(text="✅ 同意するボタンを押すと全チャンネルが解放されます")

    for member in unagreed:
        view = TermsView(str(member.id), guild_id, member.display_name)
        try:
            await terms_ch.send(
                content=f"{member.mention} **利用規約をご確認の上、同意をお願いします。**",
                embed=embed,
                view=view
            )
        except Exception:
            pass

    await interaction.followup.send(
        f"✅ 未同意メンバー {len(unagreed)}名 に規約を送信しました。",
        ephemeral=True
    )

@bot.tree.command(name="agreed_list", description="【管理者】規約同意済みメンバー一覧を表示")
@app_commands.checks.has_permissions(manage_guild=True)
async def agreed_list(interaction: discord.Interaction):
    agreed = load_agreed()
    guild_id = str(interaction.guild_id)
    members  = agreed.get(guild_id, {})
    if not members:
        await interaction.response.send_message("📭 同意済みメンバーはいません。", ephemeral=True)
        return
    embed = discord.Embed(
        title=f"✅ 規約同意済みメンバー（{len(members)}名）",
        color=0x00cc66,
        timestamp=now_jst()
    )
    for uid, info in list(members.items())[:25]:
        embed.add_field(
            name=info.get("name", uid),
            value=f"同意日時: {info.get('agreed_at', '不明')}",
            inline=True
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

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
    today = now_jst()
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
SUBMISSION_FILE = "submissions.json"  # 提出済み記録

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

# ========== 提出管理 ==========

def load_submissions():
    if os.path.exists(SUBMISSION_FILE):
        with open(SUBMISSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_submissions(data):
    with open(SUBMISSION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def mark_submitted(athlete_id: str, date: str):
    """指定日に提出済みとしてマーク"""
    subs = load_submissions()
    if athlete_id not in subs:
        subs[athlete_id] = []
    if date not in subs[athlete_id]:
        subs[athlete_id].append(date)
    save_submissions(subs)


# ========== 選手情報ヘルパー ==========

def get_athlete_icu_id(athlete_data) -> str:
    """新旧両形式からICU IDを返す"""
    if isinstance(athlete_data, dict):
        return athlete_data.get("icu_id", "")
    return athlete_data  # 旧形式: 文字列

def get_athlete_discord_id(athlete_data) -> str | None:
    """新形式からDiscord IDを返す（旧形式はNone）"""
    if isinstance(athlete_data, dict):
        return athlete_data.get("discord_id")
    return None

# ========== 疲労検知 ==========

async def fetch_icu_activities_range(api_key: str, athlete_id: str, oldest: str, newest: str) -> list:
    """指定期間のICUデータを取得（ランニングのみ）"""
    url = f"https://intervals.icu/api/v1/athlete/{athlete_id}/activities"
    params = {"oldest": oldest, "newest": newest}
    auth = aiohttp.BasicAuth("API_KEY", api_key)
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, auth=auth) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            if not isinstance(data, list):
                return []
            # ランニング・自転車のみフィルタ
            RUN_TYPES  = {"Run", "VirtualRun"}
            RIDE_TYPES = {"Ride", "VirtualRide", "MountainBikeRide", "GravelRide"}
            ALLOWED    = RUN_TYPES | RIDE_TYPES
            return [a for a in data if a.get("type") in ALLOWED or
                    a.get("sport_type") in ALLOWED or
                    str(a.get("type", "")).lower() in ("run", "ride")]

def calc_fatigue_stats(activities: list) -> dict:
    """HR・ペース・TSSから疲労指標を算出（ランニングのみ想定）"""
    hrs, paces, tss_list, loads = [], [], [], []
    for act in activities:
        hr = act.get("average_heartrate")
        if hr is not None:
            hrs.append(hr)
        speed = act.get("average_speed") or 0
        dist = act.get("distance") or 0
        if speed and dist:
            paces.append(1000 / speed)  # sec/km
        tss = act.get("icu_training_load") or act.get("training_load") or act.get("tss")
        if tss is not None:
            tss_list.append(tss)
        load = act.get("icu_rpe_load") or act.get("session_rpe")
        if load is not None:
            loads.append(load)

    return {
        "count": len(activities),
        "avg_hr": round(sum(hrs) / len(hrs), 1) if hrs else None,
        "avg_pace_sec": round(sum(paces) / len(paces), 1) if paces else None,
        "total_tss": round(sum(tss_list), 1) if tss_list else None,
        "avg_tss": round(sum(tss_list) / len(tss_list), 1) if tss_list else None,
        "total_distance_km": round(sum(act.get("distance") or 0 for act in activities) / 1000, 1),
    }

def detect_fatigue(week: dict, month: dict, three_month: dict) -> list:
    """疲労シグナルを検出してメッセージリストを返す"""
    warnings = []

    # ① 3ヶ月平均と比較してHRが上昇（ペース同等以下でも心拍増加）
    if (week.get("avg_hr") and three_month.get("avg_hr") and
            week["avg_hr"] > three_month["avg_hr"] * 1.05):
        delta = round(week["avg_hr"] - three_month["avg_hr"], 1)
        warnings.append(f"❤️ 今週の平均心拍が3ヶ月平均より **+{delta} bpm** 高い（疲労蓄積の可能性）")

    # ② ペース低下（pace_secが大きい＝遅い）
    if (week.get("avg_pace_sec") and three_month.get("avg_pace_sec") and
            week["avg_pace_sec"] > three_month["avg_pace_sec"] * 1.04):
        w_pace = f"{int(week['avg_pace_sec']//60)}:{int(week['avg_pace_sec']%60):02d}"
        b_pace = f"{int(three_month['avg_pace_sec']//60)}:{int(three_month['avg_pace_sec']%60):02d}"
        warnings.append(f"🏃 今週の平均ペース **{w_pace}/km** が3ヶ月平均 {b_pace}/km より低下")

    # ③ 週間TSSが1ヶ月平均の週換算より25%以上高い（過負荷）
    if (week.get("total_tss") and month.get("total_tss") and month["count"] > 0):
        monthly_weekly_avg = (month["total_tss"] / month["count"]) * 7 if month["count"] else 0
        if monthly_weekly_avg > 0 and week["total_tss"] > monthly_weekly_avg * 1.25:
            warnings.append(f"⚡ 今週のTSS **{week['total_tss']}** が月間週平均の125%超（過負荷注意）")

    # ④ 週の練習回数が急増（3ヶ月平均の週換算より50%以上増）
    if three_month.get("count"):
        three_month_weekly_avg = three_month["count"] / 13  # 約13週
        if week["count"] > three_month_weekly_avg * 1.5 and week["count"] >= 2:
            warnings.append(f"📅 今週の練習回数 **{week['count']}回** が3ヶ月平均の1.5倍超（急増注意）")

    return warnings

def pace_sec_to_str(sec: float) -> str:
    if not sec:
        return "—"
    return f"{int(sec//60)}:{int(sec%60):02d}"

# ========== AIコメント生成 ==========

async def generate_ai_comment(athlete_name: str, activity: dict, detail: dict,
                               history_week: list, history_month: list) -> str:
    """Claudeがコーチ視点で練習コメントを生成"""

    def summarize(acts: list) -> dict:
        if not acts:
            return {}
        paces, hrs, tss_list = [], [], []
        for a in acts:
            sp = a.get("average_speed", 0)
            if sp:
                paces.append(1000 / sp)
            hr = a.get("average_heartrate")
            if hr:
                hrs.append(hr)
            tss = a.get("icu_training_load") or a.get("training_load") or a.get("tss")
            if tss:
                tss_list.append(tss)
        return {
            "avg_pace_sec": round(sum(paces)/len(paces), 1) if paces else None,
            "avg_hr": round(sum(hrs)/len(hrs), 1) if hrs else None,
            "avg_tss": round(sum(tss_list)/len(tss_list), 1) if tss_list else None,
            "count": len(acts),
        }

    today_speed = activity.get("average_speed", 0)
    today_pace_str = pace_sec_to_str(1000 / today_speed) if today_speed else "不明"
    today_hr = activity.get("average_heartrate")
    today_tss = (activity.get("icu_training_load") or activity.get("training_load")
                 or activity.get("tss"))
    today_distance = round(activity.get("distance", 0) / 1000, 2)

    week_sum = summarize(history_week)
    month_sum = summarize(history_month)

    context = f"""
選手名: {athlete_name}
今日の練習:
  距離: {today_distance} km
  ペース: {today_pace_str}/km
  平均心拍: {today_hr or '不明'} bpm
  TSS: {today_tss or '不明'}
  練習名: {activity.get('name', '不明')}

過去7日間の平均:
  ペース: {pace_sec_to_str(week_sum.get('avg_pace_sec'))}/km
  平均心拍: {week_sum.get('avg_hr') or '不明'} bpm
  平均TSS: {week_sum.get('avg_tss') or '不明'}
  練習回数: {week_sum.get('count', 0)}回

過去30日間の平均:
  ペース: {pace_sec_to_str(month_sum.get('avg_pace_sec'))}/km
  平均心拍: {month_sum.get('avg_hr') or '不明'} bpm
  平均TSS: {month_sum.get('avg_tss') or '不明'}
  練習回数: {month_sum.get('count', 0)}回
"""

    prompt = f"""あなたは中長距離ランナーのコーチです。以下のデータを見て、コーチ視点で短いコメントを2〜3つ生成してください。
各コメントは1行で完結させ、箇条書き（・）で出力してください。
具体的な数値の変化（%や秒差）を含め、良い点・改善点・注意点をバランスよく伝えてください。
余計な前置きや説明は不要です。コメントのみを出力してください。

{context}"""

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    body = {
        "model": "claude-opus-4-6",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.anthropic.com/v1/messages",
                                    headers=headers, json=body) as resp:
                if resp.status != 200:
                    return ""
                result = await resp.json()
                return result["content"][0]["text"].strip()
    except Exception:
        return ""

async def fetch_icu_activities(api_key: str, athlete_id: str, date: str = None) -> list:
    """Interval.icuから練習データを取得（ランニングのみ）"""
    if not date:
        date = (now_jst() - timedelta(days=1)).strftime("%Y-%m-%d")

    url = f"https://intervals.icu/api/v1/athlete/{athlete_id}/activities"
    params = {"oldest": date, "newest": date}
    auth = aiohttp.BasicAuth("API_KEY", api_key)

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, auth=auth) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            if not isinstance(data, list):
                return []
            # ランニング・自転車のみフィルタ
            RUN_TYPES  = {"Run", "VirtualRun"}
            RIDE_TYPES = {"Ride", "VirtualRide", "MountainBikeRide", "GravelRide"}
            ALLOWED    = RUN_TYPES | RIDE_TYPES
            return [a for a in data if a.get("type") in ALLOWED or
                    a.get("sport_type") in ALLOWED or
                    str(a.get("type", "")).lower() in ("run", "ride")]

async def fetch_icu_activity_detail(api_key: str, athlete_id: str, activity_id: str) -> dict:
    """活動の詳細（ゾーン・負荷）を取得"""
    url = f"https://intervals.icu/api/v1/athlete/{athlete_id}/activities/{activity_id}"
    auth = aiohttp.BasicAuth("API_KEY", api_key)

    async with aiohttp.ClientSession() as session:
        async with session.get(url, auth=auth) as resp:
            if resp.status != 200:
                return {}
            data = await resp.json()
            # APIがlistを返す場合は最初の要素を使用、dictならそのまま
            if isinstance(data, list):
                return data[0] if data else {}
            return data if isinstance(data, dict) else {}

def format_icu_embed(activity: dict, detail: dict, athlete_name: str) -> discord.Embed:
    """Interval.icuデータをEmbedに整形"""
    name = activity.get("name", "練習")
    date = activity.get("start_date_local", "")[:10]

    embed = discord.Embed(
        title=f"📊 {athlete_name} の練習データ",
        description=f"**{name}** — {date}",
        color=0x4361ee,
        timestamp=now_jst()
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
    """
    定時レポート送信:
      - 練習データ + 統計サマリー  → コーチにDM
      - 疲労検知アラート           → 選手本人にDM（検知時のみ）
      - AIコーチコメント           → 選手本人 + コーチ 両方にDM
      - 未提出アラート             → コーチにDM
    """
    coach = bot_instance.get_user(int(coach_id))
    if not coach:
        return

    if not date:
        date = (now_jst() - timedelta(days=1)).strftime("%Y-%m-%d")

    today = datetime.strptime(date, "%Y-%m-%d")

    oldest_week  = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    oldest_month = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    oldest_3m    = (today - timedelta(days=90)).strftime("%Y-%m-%d")

    sent = 0
    no_submit = []

    for athlete_name, athlete_data in athletes.items():
        icu_id      = get_athlete_icu_id(athlete_data)
        discord_id  = get_athlete_discord_id(athlete_data)

        # 選手のDiscordユーザーオブジェクトを取得（DM送信用）
        athlete_user = None
        if discord_id:
            try:
                athlete_user = bot_instance.get_user(int(discord_id)) or await bot_instance.fetch_user(int(discord_id))
            except Exception:
                athlete_user = None

        activities = await fetch_icu_activities(api_key, icu_id, date)

        # ── 未提出アラート ──
        if not activities:
            no_submit.append((athlete_name, athlete_user))
            continue

        mark_submitted(icu_id, date)

        for act in activities[:1]:
            detail = await fetch_icu_activity_detail(api_key, icu_id, act.get("id", ""))

            # 期間データ取得
            acts_week  = await fetch_icu_activities_range(api_key, icu_id, oldest_week,  date)
            acts_month = await fetch_icu_activities_range(api_key, icu_id, oldest_month, date)
            acts_3m    = await fetch_icu_activities_range(api_key, icu_id, oldest_3m,    date)

            stats_week  = calc_fatigue_stats(acts_week)
            stats_month = calc_fatigue_stats(acts_month)
            stats_3m    = calc_fatigue_stats(acts_3m)
            fatigue_warnings = detect_fatigue(stats_week, stats_month, stats_3m)

            # ── コーチ向けレポートEmbed（全情報） ──
            coach_embed = format_icu_embed(act, detail, athlete_name)

            stats_lines = (
                f"**7日間** — {stats_week.get('count',0)}回 ｜ {stats_week.get('total_distance_km',0)}km"
                f" ｜ HR {stats_week.get('avg_hr') or '—'} bpm ｜ TSS {stats_week.get('total_tss') or '—'}\n"
                f"**30日間** — {stats_month.get('count',0)}回 ｜ {stats_month.get('total_distance_km',0)}km"
                f" ｜ HR {stats_month.get('avg_hr') or '—'} bpm ｜ TSS {stats_month.get('total_tss') or '—'}\n"
                f"**90日間** — {stats_3m.get('count',0)}回 ｜ {stats_3m.get('total_distance_km',0)}km"
                f" ｜ HR {stats_3m.get('avg_hr') or '—'} bpm ｜ TSS {stats_3m.get('total_tss') or '—'}"
            )
            coach_embed.add_field(name="📈 練習負荷統計（7/30/90日）", value=stats_lines, inline=False)

            if fatigue_warnings:
                coach_embed.add_field(
                    name="⚠️ 疲労検知アラート",
                    value="\n".join(fatigue_warnings),
                    inline=False
                )

            # ── AIコメント生成 ──
            ai_comment = await generate_ai_comment(athlete_name, act, detail, acts_week, acts_month)
            if ai_comment:
                coach_embed.add_field(name="🤖 AIコーチコメント", value=ai_comment, inline=False)

            # コーチにDM送信
            try:
                await coach.send(embed=coach_embed)
                sent += 1
            except Exception:
                pass

            # ── 選手へのDM送信 ──
            if athlete_user:

                # 疲労アラートがある場合 → 選手にDM
                if fatigue_warnings:
                    fatigue_embed = discord.Embed(
                        title="⚠️ 疲労検知アラート",
                        description=f"**{date}** のデータを元に疲労の兆候が検出されました。",
                        color=0xff6b35,
                        timestamp=now_jst()
                    )
                    fatigue_embed.add_field(
                        name="検出された項目",
                        value="\n".join(fatigue_warnings),
                        inline=False
                    )
                    fatigue_embed.add_field(
                        name="📈 参考データ（7日間）",
                        value=(
                            f"練習回数: {stats_week.get('count',0)}回 ｜ {stats_week.get('total_distance_km',0)}km\n"
                            f"平均心拍: {stats_week.get('avg_hr') or '—'} bpm ｜ "
                            f"平均ペース: {pace_sec_to_str(stats_week.get('avg_pace_sec'))}/km\n"
                            f"TSS合計: {stats_week.get('total_tss') or '—'}"
                        ),
                        inline=False
                    )
                    fatigue_embed.set_footer(text="無理せず休養も大切にしてください 🙏")
                    try:
                        await athlete_user.send(
                            content="📩 **PROJECT NN コーチングシステムからお知らせ**",
                            embed=fatigue_embed
                        )
                    except Exception:
                        pass
                    # コーチにも同じ疲労アラートをDM
                    try:
                        await coach.send(
                            content=f"⚠️ **{athlete_name} に疲労検知アラートを送信しました**",
                            embed=fatigue_embed
                        )
                    except Exception:
                        pass

                # AIコメントがある場合 → 選手にDM
                if ai_comment:
                    ai_embed = discord.Embed(
                        title=f"🤖 {date} の練習フィードバック",
                        description=f"**{act.get('name', '練習')}** のデータを元にコメントを生成しました。",
                        color=0x4361ee,
                        timestamp=now_jst()
                    )
                    # 今日の練習サマリーも添付
                    dist_km = round(act.get("distance", 0) / 1000, 2)
                    speed = act.get("average_speed", 0)
                    pace_str = pace_sec_to_str(1000 / speed) if speed else "—"
                    hr = act.get("average_heartrate")
                    tss = act.get("icu_training_load") or act.get("training_load") or act.get("tss")
                    ai_embed.add_field(
                        name="📊 本日の練習",
                        value=(
                            f"距離: **{dist_km} km** ｜ ペース: **{pace_str}/km**\n"
                            f"平均心拍: {int(hr) if hr else '—'} bpm ｜ TSS: {tss or '—'}"
                        ),
                        inline=False
                    )
                    ai_embed.add_field(name="💬 AIコメント", value=ai_comment, inline=False)
                    ai_embed.set_footer(text="PROJECT NN | Interval.icu データより自動生成")
                    try:
                        await athlete_user.send(
                            content="📩 **本日の練習フィードバックが届きました！**",
                            embed=ai_embed
                        )
                    except Exception:
                        pass

    # ── 未提出選手：コーチにまとめてアラート＋選手本人にも個別DM ──
    if no_submit:
        # コーチへまとめて通知
        alert_embed = discord.Embed(
            title="🚨 練習未提出アラート",
            description=f"**{date}** の練習データが届いていない選手がいます。",
            color=0xff4444,
            timestamp=now_jst()
        )
        alert_embed.add_field(
            name=f"未提出選手 ({len(no_submit)}名)",
            value="\n".join(f"• {name}" for name, _ in no_submit),
            inline=False
        )
        alert_embed.set_footer(text="Interval.icu にデータが登録されていない可能性があります")
        try:
            await coach.send(embed=alert_embed)
        except Exception:
            pass

        # 選手本人へ個別DM
        for athlete_name, athlete_user in no_submit:
            if not athlete_user:
                continue
            athlete_alert = discord.Embed(
                title="📋 練習記録の未提出のお知らせ",
                description=f"**{date}** の練習データがまだ Interval.icu に登録されていません。",
                color=0xff9900,
                timestamp=now_jst()
            )
            athlete_alert.add_field(
                name="対応をお願いします",
                value="練習を行った場合はアプリを同期してください。\nお休みの場合はコーチまでご連絡ください。",
                inline=False
            )
            athlete_alert.set_footer(text="PROJECT NN コーチングシステム")
            try:
                await athlete_user.send(
                    content="📩 **練習記録の未提出があります**",
                    embed=athlete_alert
                )
            except Exception:
                pass

    if sent == 0 and not no_submit:
        try:
            await coach.send(f"📭 {date} の練習データはありませんでした。")
        except Exception:
            pass

# ========== 定時送信タスク ==========

WEEKLY_SCHEDULE_FILE = "icu_weekly_schedule.json"
WEEKDAY_MAP = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}
def load_weekly_schedule():
    if os.path.exists(WEEKLY_SCHEDULE_FILE):
        with open(WEEKLY_SCHEDULE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_weekly_schedule(data):
    with open(WEEKLY_SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

async def send_weekly_fatigue_report(bot_instance, coach_id: str, api_key: str, athletes: dict):
    """週次疲労分析レポートをコーチ・選手にDM送信"""
    coach = bot_instance.get_user(int(coach_id))
    if not coach:
        return

    today = now_jst()
    oldest_week  = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    oldest_month = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    oldest_3m    = (today - timedelta(days=90)).strftime("%Y-%m-%d")
    base_date    = today.strftime("%Y-%m-%d")

    for athlete_name, athlete_data in athletes.items():
        icu_id     = get_athlete_icu_id(athlete_data)
        discord_id = get_athlete_discord_id(athlete_data)

        athlete_user = None
        if discord_id:
            try:
                athlete_user = bot_instance.get_user(int(discord_id)) or await bot_instance.fetch_user(int(discord_id))
            except Exception:
                pass

        acts_week  = await fetch_icu_activities_range(api_key, icu_id, oldest_week,  base_date)
        acts_month = await fetch_icu_activities_range(api_key, icu_id, oldest_month, base_date)
        acts_3m    = await fetch_icu_activities_range(api_key, icu_id, oldest_3m,    base_date)

        s_w = calc_fatigue_stats(acts_week)
        s_m = calc_fatigue_stats(acts_month)
        s_3 = calc_fatigue_stats(acts_3m)
        warnings = detect_fatigue(s_w, s_m, s_3)

        def stat_row(s: dict) -> str:
            p = pace_sec_to_str(s.get("avg_pace_sec"))
            return (
                f"練習: **{s.get('count',0)}回** ｜ {s.get('total_distance_km',0)}km\n"
                f"avg HR: {s.get('avg_hr') or '—'} bpm ｜ ペース: {p}/km\n"
                f"TSS合計: {s.get('total_tss') or '—'} ｜ avg: {s.get('avg_tss') or '—'}"
            )

        # ── コーチ向けEmbed ──
        coach_embed = discord.Embed(
            title=f"📊 週次疲労分析レポート — {athlete_name}",
            description=f"集計基準日: {base_date}",
            color=0x7b2ff7,
            timestamp=now_jst()
        )
        coach_embed.add_field(name="📅 7日間",  value=stat_row(s_w), inline=True)
        coach_embed.add_field(name="📆 30日間", value=stat_row(s_m), inline=True)
        coach_embed.add_field(name="📊 90日間", value=stat_row(s_3), inline=True)

        if warnings:
            coach_embed.add_field(name="⚠️ 疲労シグナル", value="\n".join(warnings), inline=False)
        else:
            coach_embed.add_field(name="✅ 疲労シグナル", value="今週は疲労の兆候は検出されませんでした。", inline=False)
        coach_embed.set_footer(text="PROJECT NN | 週次自動レポート")

        try:
            await coach.send(content=f"📋 **{athlete_name} の週次疲労分析レポートです**", embed=coach_embed)
        except Exception:
            pass

        # ── 選手向けEmbed（警告がある場合のみ送信） ──
        if athlete_user and warnings:
            athlete_embed = discord.Embed(
                title="⚠️ 週次疲労分析レポート",
                description=f"今週（{oldest_week} 〜 {base_date}）のデータを分析しました。",
                color=0xff6b35,
                timestamp=now_jst()
            )
            athlete_embed.add_field(
                name="📅 今週のサマリー",
                value=stat_row(s_w),
                inline=False
            )
            athlete_embed.add_field(
                name="⚠️ 検出された疲労シグナル",
                value="\n".join(warnings),
                inline=False
            )
            athlete_embed.set_footer(text="無理せず休養も大切にしてください 🙏 | PROJECT NN")
            try:
                await athlete_user.send(
                    content="📩 **今週の疲労分析レポートが届きました**",
                    embed=athlete_embed
                )
            except Exception:
                pass

@tasks.loop(minutes=1)
async def icu_scheduler():
    """毎分チェックして日次・週次を送信"""
    now      = now_jst()
    now_hm   = now.strftime("%H:%M")
    weekday  = now.weekday()  # 0=月 〜 6=日

    daily_schedule  = load_schedule()
    weekly_schedule = load_weekly_schedule()
    icu = load_icu()

    # 日次レポート
    for coach_id, time_str in daily_schedule.items():
        if time_str == now_hm:
            athletes = icu.get(coach_id, {}).get("athletes", {})
            api_key  = icu.get(coach_id, {}).get("api_key", "")
            if api_key and athletes:
                await send_icu_report(bot, coach_id, api_key, athletes)

    # 週次疲労レポート
    for coach_id, cfg in weekly_schedule.items():
        if cfg.get("weekday") == weekday and cfg.get("time") == now_hm:
            athletes = icu.get(coach_id, {}).get("athletes", {})
            api_key  = icu.get(coach_id, {}).get("api_key", "")
            if api_key and athletes:
                await send_weekly_fatigue_report(bot, coach_id, api_key, athletes)

@bot.listen("on_ready")
async def start_scheduler():
    if not icu_scheduler.is_running():
        icu_scheduler.start()

# ========== Interval.icu コマンド ==========

@bot.tree.command(name="icu_setup", description="【管理者】Interval.icu APIキー・コーチ・選手を登録する")
@app_commands.describe(
    coach="DM送信先のコーチ（メンション）",
    api_key="Interval.icu の APIキー",
    athlete_name="選手の名前",
    athlete_id="選手のInterval.icuアスリートID",
    athlete_member="選手のDiscordアカウント（メンション）"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def icu_setup(interaction: discord.Interaction, coach: discord.Member, api_key: str,
                    athlete_name: str, athlete_id: str, athlete_member: discord.Member):
    icu = load_icu()
    coach_id = str(coach.id)

    if coach_id not in icu:
        icu[coach_id] = {"api_key": api_key, "athletes": {}}

    icu[coach_id]["api_key"] = api_key
    icu[coach_id]["athletes"][athlete_name] = {
        "icu_id": athlete_id,
        "discord_id": str(athlete_member.id)
    }
    save_icu(icu)

    embed = discord.Embed(title="✅ Interval.icu 設定完了！", color=0x00cc66)
    embed.add_field(name="コーチ", value=coach.mention, inline=True)
    embed.add_field(name="選手名", value=athlete_name, inline=True)
    embed.add_field(name="ICU ID", value=athlete_id, inline=True)
    embed.add_field(name="Discord", value=athlete_member.mention, inline=True)
    embed.set_footer(text="/icu_setup を繰り返して選手を追加できます")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="icu", description="選手のInterval.icu練習データを取得")
@app_commands.describe(
    coach="対象コーチ（メンション）",
    athlete_name="選手の名前",
    date="日付（例: 2026-03-14）省略すると昨日"
)
async def icu(interaction: discord.Interaction, coach: discord.Member, athlete_name: str, date: str = None):
    await interaction.response.defer()
    icu_data = load_icu()
    coach_id = str(coach.id)

    if coach_id not in icu_data:
        await interaction.followup.send(f"❌ {coach.mention} は `/icu_setup` で登録されていません。", ephemeral=True)
        return

    api_key = icu_data[coach_id]["api_key"]
    athletes = icu_data[coach_id]["athletes"]

    if athlete_name not in athletes:
        names = "、".join(athletes.keys())
        await interaction.followup.send(f"❌ 選手が見つかりません。登録済み: {names}", ephemeral=True)
        return

    athlete_id = get_athlete_icu_id(athletes[athlete_name])
    target_date = date or (now_jst() - timedelta(days=1)).strftime("%Y-%m-%d")

    activities = await fetch_icu_activities(api_key, athlete_id, target_date)
    if not activities:
        await interaction.followup.send(f"📭 {athlete_name} の {target_date} の練習データはありません。")
        return

    today = datetime.strptime(target_date, "%Y-%m-%d")
    for act in activities[:1]:
        detail = await fetch_icu_activity_detail(api_key, athlete_id, act.get("id", ""))
        embed = format_icu_embed(act, detail, athlete_name)

        # 疲労検知
        oldest_week  = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        oldest_month = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        oldest_3m    = (today - timedelta(days=90)).strftime("%Y-%m-%d")
        acts_week  = await fetch_icu_activities_range(api_key, athlete_id, oldest_week, target_date)
        acts_month = await fetch_icu_activities_range(api_key, athlete_id, oldest_month, target_date)
        acts_3m    = await fetch_icu_activities_range(api_key, athlete_id, oldest_3m, target_date)
        stats_week  = calc_fatigue_stats(acts_week)
        stats_month = calc_fatigue_stats(acts_month)
        stats_3m    = calc_fatigue_stats(acts_3m)
        fatigue_warnings = detect_fatigue(stats_week, stats_month, stats_3m)
        if fatigue_warnings:
            embed.add_field(
                name="⚠️ 疲労検知アラート",
                value="\n".join(fatigue_warnings),
                inline=False
            )

        stats_lines = (
            f"**7日間** — {stats_week.get('count',0)}回 ｜ {stats_week.get('total_distance_km',0)}km"
            f"｜ avg HR {stats_week.get('avg_hr') or '—'} bpm ｜ TSS {stats_week.get('total_tss') or '—'}\n"
            f"**30日間** — {stats_month.get('count',0)}回 ｜ {stats_month.get('total_distance_km',0)}km"
            f"｜ avg HR {stats_month.get('avg_hr') or '—'} bpm ｜ TSS {stats_month.get('total_tss') or '—'}\n"
            f"**90日間** — {stats_3m.get('count',0)}回 ｜ {stats_3m.get('total_distance_km',0)}km"
            f"｜ avg HR {stats_3m.get('avg_hr') or '—'} bpm ｜ TSS {stats_3m.get('total_tss') or '—'}"
        )
        embed.add_field(name="📈 練習負荷統計", value=stats_lines, inline=False)

        # AIコメント
        ai_comment = await generate_ai_comment(athlete_name, act, detail, acts_week, acts_month)
        if ai_comment:
            embed.add_field(name="🤖 AIコーチコメント", value=ai_comment, inline=False)

        await interaction.followup.send(embed=embed)

@bot.tree.command(name="icu_settime", description="【管理者】Interval.icuレポートの自動送信時刻を設定")
@app_commands.describe(
    coach="対象コーチ（メンション）",
    time="送信時刻（例: 09:00）"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def icu_settime(interaction: discord.Interaction, coach: discord.Member, time: str):
    # 時刻フォーマット確認
    try:
        datetime.strptime(time, "%H:%M")
    except ValueError:
        await interaction.response.send_message("❌ 時刻の形式が違います。例: `09:00`", ephemeral=True)
        return

    schedule = load_schedule()
    schedule[str(coach.id)] = time
    save_schedule(schedule)

    embed = discord.Embed(title="✅ 自動送信時刻を設定しました！", color=0x00cc66)
    embed.add_field(name="コーチ", value=coach.mention, inline=True)
    embed.add_field(name="送信時刻", value=f"毎日 **{time}**", inline=True)
    embed.set_footer(text="前日の全選手の練習データがDMに届きます")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="icu_fatigue", description="選手の疲労状況を確認する")
@app_commands.describe(
    coach="対象コーチ（メンション）",
    athlete_name="選手の名前",
    date="基準日（省略で今日）"
)
async def icu_fatigue(interaction: discord.Interaction, coach: discord.Member,
                      athlete_name: str, date: str = None):
    await interaction.response.defer()
    icu_data = load_icu()
    coach_id = str(coach.id)
    if coach_id not in icu_data:
        await interaction.followup.send("❌ コーチが登録されていません。", ephemeral=True)
        return
    api_key = icu_data[coach_id]["api_key"]
    athletes = icu_data[coach_id]["athletes"]
    if athlete_name not in athletes:
        await interaction.followup.send(f"❌ 選手 '{athlete_name}' が見つかりません。", ephemeral=True)
        return

    athlete_id = get_athlete_icu_id(athletes[athlete_name])
    target_date = date or now_jst().strftime("%Y-%m-%d")
    today = datetime.strptime(target_date, "%Y-%m-%d")

    oldest_week  = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    oldest_month = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    oldest_3m    = (today - timedelta(days=90)).strftime("%Y-%m-%d")

    acts_week  = await fetch_icu_activities_range(api_key, athlete_id, oldest_week, target_date)
    acts_month = await fetch_icu_activities_range(api_key, athlete_id, oldest_month, target_date)
    acts_3m    = await fetch_icu_activities_range(api_key, athlete_id, oldest_3m, target_date)

    s_w = calc_fatigue_stats(acts_week)
    s_m = calc_fatigue_stats(acts_month)
    s_3 = calc_fatigue_stats(acts_3m)
    warnings = detect_fatigue(s_w, s_m, s_3)

    embed = discord.Embed(
        title=f"🔬 {athlete_name} 疲労分析レポート",
        description=f"基準日: {target_date}",
        color=0xff6b35,
        timestamp=now_jst()
    )

    def row(s: dict, label: str) -> str:
        p = pace_sec_to_str(s.get("avg_pace_sec"))
        return (f"練習: **{s.get('count',0)}回** ｜ {s.get('total_distance_km',0)}km\n"
                f"avg HR: {s.get('avg_hr') or '—'} bpm ｜ ペース: {p}\n"
                f"TSS合計: {s.get('total_tss') or '—'} ｜ avg: {s.get('avg_tss') or '—'}")

    embed.add_field(name="📅 7日間",  value=row(s_w, "週"), inline=True)
    embed.add_field(name="📆 30日間", value=row(s_m, "月"), inline=True)
    embed.add_field(name="📊 90日間", value=row(s_3, "3ヶ月"), inline=True)

    if warnings:
        embed.add_field(name="⚠️ 疲労シグナル", value="\n".join(warnings), inline=False)
    else:
        embed.add_field(name="✅ 疲労シグナル", value="現時点で疲労の兆候は検出されていません。", inline=False)

    embed.set_footer(text="Interval.icu データを元に算出")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="icu_setweekly", description="【管理者】週次疲労分析レポートの自動送信を設定する")
@app_commands.describe(
    coach="対象コーチ（メンション）",
    weekday="送信する曜日",
    time="送信時刻（例: 09:00）"
)
@app_commands.choices(weekday=[
    app_commands.Choice(name="月曜日", value="月"),
    app_commands.Choice(name="火曜日", value="火"),
    app_commands.Choice(name="水曜日", value="水"),
    app_commands.Choice(name="木曜日", value="木"),
    app_commands.Choice(name="金曜日", value="金"),
    app_commands.Choice(name="土曜日", value="土"),
    app_commands.Choice(name="日曜日", value="日"),
])
@app_commands.checks.has_permissions(manage_guild=True)
async def icu_setweekly(interaction: discord.Interaction, coach: discord.Member,
                        weekday: app_commands.Choice[str], time: str):
    try:
        datetime.strptime(time, "%H:%M")
    except ValueError:
        await interaction.response.send_message("❌ 時刻の形式が違います。例: `09:00`", ephemeral=True)
        return

    weekly = load_weekly_schedule()
    weekly[str(coach.id)] = {"weekday": WEEKDAY_MAP[weekday.value], "time": time}
    save_weekly_schedule(weekly)

    embed = discord.Embed(title="✅ 週次疲労レポートを設定しました！", color=0x7b2ff7)
    embed.add_field(name="コーチ", value=coach.mention, inline=True)
    embed.add_field(name="送信タイミング", value=f"毎週**{weekday.name}** {time}", inline=True)
    embed.set_footer(text="全選手の疲労分析レポートがコーチ・選手にDMで届きます")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="icu_cancelweekly", description="【管理者】週次疲労レポートの自動送信をキャンセル")
@app_commands.describe(coach="対象コーチ（メンション）")
@app_commands.checks.has_permissions(manage_guild=True)
async def icu_cancelweekly(interaction: discord.Interaction, coach: discord.Member):
    weekly = load_weekly_schedule()
    coach_id = str(coach.id)
    if coach_id in weekly:
        del weekly[coach_id]
        save_weekly_schedule(weekly)
        await interaction.response.send_message(f"✅ {coach.mention} の週次疲労レポートをキャンセルしました。", ephemeral=True)
    else:
        await interaction.response.send_message("❌ 週次疲労レポートは設定されていません。", ephemeral=True)

@bot.tree.command(name="icu_canceltime", description="【管理者】自動送信をキャンセル")
@app_commands.checks.has_permissions(manage_guild=True)
async def icu_canceltime(interaction: discord.Interaction, coach: discord.Member):
    schedule = load_schedule()
    coach_id = str(coach.id)
    if coach_id in schedule:
        del schedule[coach_id]
        save_schedule(schedule)
        await interaction.response.send_message(f"✅ {coach.mention} の自動送信をキャンセルしました。", ephemeral=True)
    else:
        await interaction.response.send_message("❌ 自動送信は設定されていません。", ephemeral=True)

@bot.tree.command(name="icu_athletes", description="【管理者】登録済み選手一覧を表示")
@app_commands.describe(coach="対象コーチ（メンション）")
@app_commands.checks.has_permissions(manage_guild=True)
async def icu_athletes(interaction: discord.Interaction, coach: discord.Member):
    icu_data = load_icu()
    coach_id = str(coach.id)

    if coach_id not in icu_data or not icu_data[coach_id].get("athletes"):
        await interaction.response.send_message(f"📭 {coach.mention} に選手が登録されていません。`/icu_setup` で登録してください。", ephemeral=True)
        return

    athletes = icu_data[coach_id]["athletes"]
    schedule = load_schedule()
    time_str = schedule.get(coach_id, "未設定")

    embed = discord.Embed(title=f"👥 {coach.display_name} の登録済み選手一覧", color=0x3399ff)
    for name, adata in athletes.items():
        icu_id = get_athlete_icu_id(adata)
        discord_id = get_athlete_discord_id(adata)
        discord_str = f"<@{discord_id}>" if discord_id else "未設定"
        embed.add_field(name=name, value=f"ICU ID: `{icu_id}`\nDiscord: {discord_str}", inline=True)
    embed.set_footer(text=f"自動送信時刻: {time_str}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ========== Discord ID紐付け ==========

@bot.tree.command(name="icu_link_discord", description="【管理者】選手のDiscordアカウントをICU選手情報に紐付ける")
@app_commands.describe(
    coach="対象コーチ（メンション）",
    athlete_name="選手の名前（/icu_setup で登録済みの名前）",
    member="選手のDiscordアカウント（メンション）"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def icu_link_discord(interaction: discord.Interaction, coach: discord.Member,
                           athlete_name: str, member: discord.Member):
    icu = load_icu()
    coach_id = str(coach.id)

    if coach_id not in icu:
        await interaction.response.send_message("❌ コーチが登録されていません。先に `/icu_setup` を実行してください。", ephemeral=True)
        return

    athletes = icu[coach_id].get("athletes", {})
    if athlete_name not in athletes:
        names = "、".join(athletes.keys())
        await interaction.response.send_message(f"❌ 選手 `{athlete_name}` が見つかりません。登録済み: {names}", ephemeral=True)
        return

    # 旧形式（文字列）の場合は辞書に変換
    current = athletes[athlete_name]
    if isinstance(current, str):
        icu[coach_id]["athletes"][athlete_name] = {
            "icu_id": current,
            "discord_id": str(member.id)
        }
    else:
        icu[coach_id]["athletes"][athlete_name]["discord_id"] = str(member.id)

    save_icu(icu)

    embed = discord.Embed(title="✅ Discord紐付け完了！", color=0x00cc66)
    embed.add_field(name="選手名", value=athlete_name, inline=True)
    embed.add_field(name="Discordアカウント", value=member.mention, inline=True)
    embed.set_footer(text="これで疲労アラート・AIコメント・未提出通知がDMで届きます")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ========== 起動 ==========
bot.run(os.environ["DISCORD_TOKEN"])
