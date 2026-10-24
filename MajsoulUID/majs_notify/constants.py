from dataclasses import dataclass

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 以下取值对齐 Unity WebGL 客户端（Lua: GameUtility / LoginMgr / NetRoute）
CLIENT_PLATFORM = "WebGL"
DETAIL_PLATFORM = "Web"
DETAIL_PLATFORM_ID = 11
ROUTE_TYPE_MAIN = 1
LOGIN_BEAT_CONTRACT = "DF2vkXCnfeXp4WoGrBGNcJBufZiMN3uP"


@dataclass(frozen=True)
class MajsoulRegion:
    tag: str
    web_url: str
    route_lang: str
    # 入口网关只用来查路由表，WS 连的是路由表里的 domain
    gateways: tuple[str, ...]
    # 来自 mall/channel_config 表的 WebGL 渠道
    currency_platforms: tuple[int, ...]


REGION_CN = MajsoulRegion(
    tag="cn",
    web_url="https://game.maj-soul.com/1/",
    route_lang="chs_t",
    gateways=(
        "https://route-2.maj-soul.com",
        "https://route-3.maj-soul.com:8443",
        "https://route-4.maj-soul.com",
        "https://route-5.maj-soul.com",
        "https://route-6.maj-soul.com",
    ),
    currency_platforms=(1, 2, 5, 6, 8, 10, 11),
)
REGION_JP = MajsoulRegion(
    tag="jp",
    web_url="https://game.mahjongsoul.com/",
    route_lang="jp",
    gateways=("https://jpgs.mahjongsoul.com", "https://jpgsbk.mahjongsoul.com"),
    currency_platforms=(1, 3, 5, 9, 12),
)
REGION_EN = MajsoulRegion(
    tag="en",
    web_url="https://mahjongsoul.game.yo-star.com/",
    route_lang="en",
    gateways=("https://engs.mahjongsoul.com", "https://engsbk.mahjongsoul.com"),
    currency_platforms=(1, 4, 5, 9, 12),
)


def yostar_region(lang: str) -> MajsoulRegion:
    # 登录命令存的是 passport 的语言码 "ja"，旧数据里也可能是 "jp"
    return REGION_JP if lang in ("ja", "jp") else REGION_EN


ModeId2Room = {
    0: "",
    1: "铜之间",
    2: "铜之间 · 四人东",
    3: "铜之间 · 四人南",
    17: "铜之间 · 三人东",
    18: "铜之间 · 三人南",
    4: "银之间",
    5: "银之间 · 四人东",
    6: "银之间 · 四人南",
    19: "银之间 · 三人东",
    20: "银之间 · 三人南",
    7: "金之间",
    8: "金之间 · 四人东",
    9: "金之间 · 四人南",
    21: "金之间 · 三人东",
    22: "金之间 · 三人南",
    10: "玉之间",
    11: "玉之间 · 四人东",
    12: "玉之间 · 四人南",
    23: "玉之间 · 三人东",
    24: "玉之间 · 三人南",
    15: "王座间 · 四人东",
    16: "王座间 · 四人南",
    25: "王座间 · 三人东",
    26: "王座间 · 三人南",
}
