import os
import sys
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from collections import defaultdict
from datetime import datetime, timedelta
import requests

def log(msg):
    print(msg)
    sys.stdout.flush()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

async def check_shutdown_time():
    await bot.wait_until_ready()
    while True:
        now = datetime.utcnow() + timedelta(hours=9)
        hour = now.hour
        if 11 <= hour or hour < 3:
            log(f"[ACTIVE] 현재 {hour}시 - 봇 실행 유지")
        else:
            log(f"[INACTIVE] 현재 {hour}시 - 비활성 시간, 대기 중")
            # 원하는 경우 command 수신 제한도 여기에 추가 가능
        await asyncio.sleep(180)  # 3분마다 상태 확인

@bot.event
async def on_ready():
    await tree.sync()
    log(f"✅ 봇 로그인 성공: {bot.user}")
    bot.loop.create_task(check_shutdown_time())
    
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
PUBG_API_KEY = os.getenv("PUBG_API_KEY")

QUEUE_MAPPING = {
    420: "솔로랭크",
    430: "일반 (소환사의 협곡)",
    440: "자유랭크",
    450: "칼바람 나락",
    1700: "아레나",
    1900: "URF",
}

TIER_AVERAGES = {
    "IRON":     {"KDA": 2.0, "CS": 120, "Gold": 9000,  "Damage": 14000, "Vision": 12},
    "BRONZE":   {"KDA": 2.2, "CS": 130, "Gold": 9800,  "Damage": 15000, "Vision": 14},
    "SILVER":   {"KDA": 2.5, "CS": 150, "Gold": 11000, "Damage": 18000, "Vision": 20},
    "GOLD":     {"KDA": 2.8, "CS": 160, "Gold": 11500, "Damage": 19000, "Vision": 22},
    "PLATINUM": {"KDA": 3.0, "CS": 170, "Gold": 12000, "Damage": 20000, "Vision": 25},
    "DIAMOND":  {"KDA": 3.3, "CS": 180, "Gold": 12500, "Damage": 21000, "Vision": 27},
}

def get_champion_name_map():
    version_url = "https://ddragon.leagueoflegends.com/api/versions.json"
    latest_version = requests.get(version_url).json()[0]
    champ_url = f"https://ddragon.leagueoflegends.com/cdn/{latest_version}/data/ko_KR/champion.json"
    data = requests.get(champ_url).json()
    return {k: v["name"] for k, v in data["data"].items()}

champion_name_map = get_champion_name_map()

def compare(val, avg):
    return "↑" if val > avg else "↓"

# 롤 전적 명령어
@tree.command(name="전적", description="롤 소환사의 최근 전적을 확인합니다 (예: Hide on bush#KR1)")
async def 전적(interaction: discord.Interaction, riot_id: str):
    await interaction.response.defer()

    if "#" not in riot_id:
        await interaction.followup.send("⚠️ `닉네임#태그` 형식으로 입력해주세요. (예: Hide on bush#KR1)")
        return

    game_name, tag_line = riot_id.split("#")
    headers = {"X-Riot-Token": RIOT_API_KEY}

    account_url = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    account_res = requests.get(account_url, headers=headers)
    if account_res.status_code != 200:
        await interaction.followup.send("❌ 소환사를 찾을 수 없습니다.")
        return

    account_data = account_res.json()
    puuid = account_data.get("puuid")
    if not puuid:
        await interaction.followup.send("❌ 유효하지 않은 Riot ID입니다.")
        return

    summoner_name = account_data.get("gameName", riot_id)
    tier = "SILVER"

    try:
        summoner_url = f"https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
        summoner_res = requests.get(summoner_url, headers=headers)
        summoner_data = summoner_res.json()

        if "id" in summoner_data:
            summoner_id = summoner_data["id"]
            rank_url = f"https://kr.api.riotgames.com/lol/league/v4/entries/by-summoner/{summoner_id}"
            rank_data = requests.get(rank_url, headers=headers).json()

            if rank_data:
                solo = next((entry for entry in rank_data if entry["queueType"] == "RANKED_SOLO_5x5"), None)
                if solo:
                    tier = solo["tier"]
    except Exception as e:
        print("[WARN] 티어 정보 불러오기 실패:", e)

    tier_avg = TIER_AVERAGES.get(tier.upper(), TIER_AVERAGES["SILVER"])

    match_ids = requests.get(
        f"https://asia.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count=10",
        headers=headers
    ).json()

    stats_by_mode = defaultdict(lambda: {
        "kills": 0, "deaths": 0, "assists": 0,
        "cs": 0, "gold": 0, "duration": 0,
        "damage": 0, "vision": 0,
        "team_damage": 0,
        "count": 0, "wins": 0,
        "champions": defaultdict(lambda: {"count": 0, "wins": 0}),
        "dates": []
    })

    for match_id in match_ids:
        match_data = requests.get(
            f"https://asia.api.riotgames.com/lol/match/v5/matches/{match_id}",
            headers=headers
        ).json()

        info = match_data["info"]
        queue_id = info.get("queueId", -1)
        game_mode = QUEUE_MAPPING.get(queue_id, f"기타 모드 ({queue_id})")
        game_time = datetime.fromtimestamp(info["gameCreation"] / 1000).strftime("%Y-%m-%d")

        total_team_damage = sum(p["totalDamageDealtToChampions"] for p in info["participants"] if p["teamId"] == 100)
        for p in info["participants"]:
            if p["puuid"] == puuid:
                stat = stats_by_mode[game_mode]
                stat["kills"] += p["kills"]
                stat["deaths"] += p["deaths"]
                stat["assists"] += p["assists"]
                stat["cs"] += p["totalMinionsKilled"] + p["neutralMinionsKilled"]
                stat["gold"] += p["goldEarned"]
                stat["damage"] += p["totalDamageDealtToChampions"]
                stat["vision"] += p["visionScore"]
                stat["duration"] += info["gameDuration"]
                stat["team_damage"] += total_team_damage
                stat["count"] += 1
                if p["win"]:
                    stat["wins"] += 1
                champ_kr = champion_name_map.get(p["championName"], p["championName"])
                stat["champions"][champ_kr]["count"] += 1
                if p["win"]:
                    stat["champions"][champ_kr]["wins"] += 1
                stat["dates"].append(game_time)
                break

    embed = discord.Embed(
        title=f"{summoner_name} 님의 최근 10경기 전적",
        description=f"(비교 기준: {tier.upper()} 평균값)",
        color=discord.Color.gold() if tier == "GOLD" else discord.Color.blue()
    )

    for mode, stat in stats_by_mode.items():
        count = stat["count"]
        if count == 0:
            continue

        if stat["dates"]:
            dates = sorted(stat["dates"])
            date_range = f"📅 날짜: {dates[0]} ~ {dates[-1]}\n"
        else:
            date_range = ""

        avg_kills = stat["kills"] / count
        avg_deaths = stat["deaths"] / count
        avg_assists = stat["assists"] / count
        kda_ratio = round((stat["kills"] + stat["assists"]) / max(1, stat["deaths"]), 2)
        avg_cs = stat["cs"] // count
        avg_gold = stat["gold"] // count
        avg_damage = stat["damage"] // count
        avg_vision = stat["vision"] // count
        avg_duration = stat["duration"] // count // 60
        winrate = round(stat["wins"] / count * 100, 1)

        champs = stat["champions"]
        top_champs = sorted(champs.items(), key=lambda c: c[1]["count"], reverse=True)[:5]
        champ_text = "\n".join([
            f"{name}: {data['count']}전 {data['wins']}승 {data['count'] - data['wins']}패 ({int(data['wins']/data['count']*100)}%)"
            for name, data in top_champs
        ]) if top_champs else "없음"

        value = (
            f"{date_range}"
            f"📊 KDA: {avg_kills:.1f}/{avg_deaths:.1f}/{avg_assists:.1f} (비율: {kda_ratio}) {compare(kda_ratio, tier_avg['KDA'])} (평균: {tier_avg['KDA']})\n"
            f"📈 CS: {avg_cs} {compare(avg_cs, tier_avg['CS'])} (평균: {tier_avg['CS']})\n"
            f"💰 골드: {avg_gold} {compare(avg_gold, tier_avg['Gold'])} (평균: {tier_avg['Gold']})\n"
            f"🗡️ 피해량: {avg_damage} {compare(avg_damage, tier_avg['Damage'])} (평균: {tier_avg['Damage']})\n"
            f"👁️ 시야점수: {avg_vision} {compare(avg_vision, tier_avg['Vision'])} (평균: {tier_avg['Vision']})\n"
            f"🟢 승률: {winrate}%\n"
            f"🎯 챔피언: \n{champ_text}"
        )

        embed.add_field(name=f"🕹️ {mode} ({count}게임)", value=value, inline=False)

    await interaction.followup.send(embed=embed)
@tree.command(name="배그전적상세", description="PUBG 최근 10경기 상세 전적")
async def 배그전적상세(interaction: discord.Interaction, nickname: str):
    await interaction.response.defer()

    try:
        nickname = nickname.strip()
        user_url = f"https://api.pubg.com/shards/steam/players?filter[playerNames]={nickname}"
        headers = {
            "Authorization": f"Bearer {PUBG_API_KEY}",
            "Accept": "application/vnd.api+json"
        }
        user_res = requests.get(user_url, headers=headers)
        user_data = user_res.json()

        if user_res.status_code != 200 or "data" not in user_data or not user_data["data"]:
            await interaction.followup.send("❌ 플레이어를 찾을 수 없습니다.")
            return

        player = user_data["data"][0]
        player_id = player["id"]
        matches_data = player.get("relationships", {}).get("matches", {}).get("data", [])
        match_ids = [m["id"] for m in matches_data[:10]]

        total = defaultdict(float)
        match_dates = []
        max_kill_distance = 0.0

        for match_id in match_ids:
            match_url = f"https://api.pubg.com/shards/steam/matches/{match_id}"
            match_res = requests.get(match_url, headers=headers)
            match_data = match_res.json()

            created_at = match_data["data"]["attributes"]["createdAt"]
            match_dates.append(created_at.split("T")[0])

            included = match_data.get("included", [])
            for entry in included:
                if entry["type"] == "participant" and entry.get("attributes", {}).get("stats", {}).get("playerId") == player_id:
                    stats = entry["attributes"]["stats"]
                    total["kills"] += stats.get("kills", 0)
                    total["damage"] += stats.get("damageDealt", 0)
                    total["timeSurvived"] += stats.get("timeSurvived", 0)
                    total["teamKills"] += stats.get("teamKills", 0)
                    total["DBNOs"] += stats.get("DBNOs", 0)
                    total["winPlace"] += stats.get("winPlace", 0)
                    total["walkDistance"] += stats.get("walkDistance", 0)
                    total["rideDistance"] += stats.get("rideDistance", 0)
                    total["swimDistance"] += stats.get("swimDistance", 0)
                    total["headshotKills"] += stats.get("headshotKills", 0)
                    total["boosts"] += stats.get("boosts", 0)
                    total["heals"] += stats.get("heals", 0)
                    max_kill_distance = max(max_kill_distance, stats.get("longestKill", 0))
                    break

        games = len(match_ids)
        if games == 0 or total["kills"] == 0:
            await interaction.followup.send("❗ 최근 경기 정보가 없습니다.")
            return

        avg_kills = round(total["kills"] / games, 2)
        avg_damage = round(total["damage"] / games, 1)
        avg_survive = int(total["timeSurvived"] / games)
        avg_survive_str = f"{avg_survive//60}분 {avg_survive%60}초"
        avg_teamkills = round(total["teamKills"] / games, 2)
        avg_rank = round(total["winPlace"] / games, 1)
        avg_dbnos = round(total["DBNOs"] / games, 1)
        total_distance_km = round((total["walkDistance"] + total["rideDistance"] + total["swimDistance"]) / games / 1000, 2)
        headshot_rate = round((total["headshotKills"] / total["kills"] * 100) if total["kills"] > 0 else 0, 1)
        avg_boosts = round(total["boosts"] / games, 1)
        avg_heals = round(total["heals"] / games, 1)

        date_range = f"{min(match_dates)} ~ {max(match_dates)}"

        embed = discord.Embed(title=f"{nickname} 님의 PUBG 최근 10경기 상세 전적", color=discord.Color.orange())
        embed.add_field(name="📊 평균 스탯", value=(
            f"📅 날짜: {date_range}\n"
            f"🔫 킬: {avg_kills} | 💥 피해량: {avg_damage}\n"
            f"🧠 기절 수: {avg_dbnos} | ☠️ 팀킬: {avg_teamkills}\n"
            f"🕒 생존 시간: {avg_survive_str} | 🏁 평균 순위: {avg_rank}등\n"
            f"💊 회복 아이템\n• 치료: {avg_heals}회 \n• 부스터: {avg_boosts}회 \n"
            f"🚶 이동 거리: {total_distance_km}km | 📏 최대 킬 거리: {int(max_kill_distance)}m\n"
            f"🎯 헤드샷률: {headshot_rate}%"
        ), inline=False)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"❌ 오류 발생: {str(e)}")
bot.run(DISCORD_TOKEN)
