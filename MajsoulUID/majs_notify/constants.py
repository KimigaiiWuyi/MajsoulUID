from dataclasses import dataclass
from urllib.parse import urlsplit

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 以下取值对齐 Unity WebGL 客户端（Lua: GameUtility / LoginMgr / NetRoute）
CLIENT_PLATFORM = "WebGL"
DETAIL_PLATFORM = "Web"
DETAIL_PLATFORM_ID = 11
# NetDefines.RouteType = Basic.Enum({'None','Main','Standby','Detect'})，从 0 开始
ROUTE_TYPE_NONE = 0
ROUTE_TYPE_MAIN = 1
ROUTE_TYPE_STANDBY = 2
ROUTE_TYPE_DETECT = 3
LOGIN_BEAT_CONTRACT = "DF2vkXCnfeXp4WoGrBGNcJBufZiMN3uP"
# LoginMgr._SendLoginBeat：失败隔 1 秒重发，最多发 2 次
LOGIN_BEAT_ATTEMPTS = 2
LOGIN_BEAT_RETRY_DELAY = 1.0
# 登录页标签：0「邮箱/账号登录」（str 3127），1「手机号码」（str 3132，只能输数字）
LOGIN_TYPE_ACCOUNT = 0
LOGIN_TYPE_PHONE = 1
# oauth2 登录类型：0 国服自己的 access_token，7 Yostar
SOCIO_TYPE_CN = 0
SOCIO_TYPE_YOSTAR = 7

# NetRoute._RequestToByte：i=(i+1)%60007，按两字节小端写进包头
REQUEST_INDEX_MOD = 60007
# RouteDelayWatcher：请求等回包超过 10 秒算超时；NetRoute:Connect 10 秒连不上就关
RPC_TIMEOUT = 10.0
CONNECT_TIMEOUT = 10.0
# NetDefines.DELAY_INF；心跳里的 delay / network_quality 都是秒 ×1000 后夹到 [0, 5000]
DELAY_INF = 1000.0
HEARTBEAT_FIELD_MAX = 5000
# NetRoute:_Loop：每 15 秒 ping 一次，全部请求和心跳算出的线路质量差超过 0.1 时 5 秒一次
ROUTE_PING_INTERVAL = 15.0
ROUTE_PING_INTERVAL_UNSTABLE = 5.0
ROUTE_QUALITY_GAP = 0.1
# GameMgr：每 6 分钟带上挂机秒数 ping 一次，挂机满 50 分钟客户端自己弹登出
LOBBY_PING_INTERVAL = 6 * 60
HANGUP_LOGOUT_SECONDS = 50 * 60
# GameMgr:_gameInit：每小时 fetchServerTime 校一次时
SERVER_TIME_INTERVAL = 3600
# NetRouteGroup 的重连间隔表 h={0.3,1,3,6,10,15}，用完还连不上就放弃
RECONNECT_DELAYS = (0.3, 1.0, 3.0, 6.0, 10.0, 15.0)

# ---- 备用线路（NetRouteGroup / NetRoute / GatewayFetcher），单位秒 ----
DELAY_GOOD_THRESHOLD = 0.3
DELAY_BAD_THRESHOLD = 1.0
GROUP_TICK_INTERVAL = 1.0
# 主线路质量超过 4 秒持续 5 秒算黑洞；重连超过 20 秒也去切备用线路
BLACK_HOLE_QUALITY = 4.0
BLACK_HOLE_SECONDS = 5.0
RECONNECT_SWITCH_AFTER = 20.0
# 上次重连 / 切换后 30 秒内不切；主线路刚开 10 秒、备用 / 探测刚开 3 秒内不切
SWITCH_COOLDOWN = 30.0
MAIN_SETTLE_SECONDS = 10.0
STANDBY_SETTLE_SECONDS = 3.0
# 备用比主线路好 0.5 秒才自动切；探测比备用好 0.1 秒才替换备用
SWITCH_MAIN_MARGIN = 0.5
SWITCH_STANDBY_MARGIN = 0.1
# NetRoute:_Loop：备用线路开满 20 分钟、探测线路开满 40 秒（没被锁定时）自动关
STANDBY_MAX_AGE = 20 * 60.0
DETECT_MAX_AGE = 40.0
# NetRoute:Detect：距上次探测结束超过 30 秒才再测，每次连发 2~5 个 ping
DETECT_INTERVAL = 30.0
# _CalcDetectCooldown：备用线路越差探测越勤，最短 3 秒，最长 3 分钟
DETECT_COOLDOWN_MIN = 3.0
DETECT_COOLDOWN_MAX = 3 * 60.0
# 各种失败后这条线路多久不再拿来探测
BAN_CONNECT_FAILED = 5 * 60.0
BAN_SWITCH_FAILED = 5 * 60.0
BAN_PREPARE_FAILED = 5 * 60.0
BAN_REPLACED = 60.0
BAN_DETECT_BAD = 10 * 60.0
BAN_DETECT_WORSE = 3 * 60.0
# GatewayFetcher.CheckFetch：成功后 2 分钟、维护中 30 秒刷新一次路由表
GATEWAY_REFRESH_INTERVAL = 2 * 60.0
GATEWAY_MAINTENANCE_REFRESH = 30.0
GATEWAY_TIMEOUT = 10.0
# Fetch：先问上次成功的网关，3 秒没结果再每批 3 个问其他的
GATEWAY_RETRY_DELAY = 3.0
GATEWAY_BATCH = 3
# GameUtility.ENetBusyState；只有 idle、normal 能用，探测只挑 idle
BUSY_IDLE = "idle"
BUSY_NORMAL = "normal"
BUSY_REJECTED = "rejected"
BUSY_REMOVED = "removed"

# UI_Entrance：503 弹封禁说明，156 进 UI_LoginQueue 排队
ERR_PROHIBITED = 503
ERR_LOGIN_QUEUE = 156
# UI_LoginQueue：每 10 秒刷新排队信息，连续出错超过 3 次报错
LOGIN_QUEUE_POLL_INTERVAL = 10.0
LOGIN_QUEUE_MAX_ERRORS = 3

IGNORED_NOTIFY = frozenset({".lq.NotifyAccountUpdate"})


@dataclass(frozen=True)
class MajsoulRegion:
    tag: str
    web_url: str
    route_lang: str
    # 入口网关只用来查路由表，WS 连的是路由表里的 domain
    gateways: tuple[str, ...]
    # 来自 mall/channel_config 表的 WebGL 渠道
    currency_platforms: tuple[int, ...]
    # 牌谱 data_url 是相对路径时拼的前缀（docs/config.json 的 prefix_url）
    record_prefix_url: str = ""

    @property
    def origin(self) -> str:
        parts = urlsplit(self.web_url)
        return f"{parts.scheme}://{parts.netloc}"


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
    record_prefix_url="https://record-old.maj-soul.com:9443/majsoul/game_record",
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


# 牌谱接口的错误码，文案取自 MajsoulData 的 info/error 表
ERR_RECORD_FETCH_LIMIT = 540
RECORD_ERROR_TEXT = {
    511: "获取牌谱失败，请稍后重试",
    540: "超出牌谱拉取限制，请稍后重试",
    541: "没有该牌谱的查看权限",
    1203: "这局游戏不存在",
}


SERVER_NAME = {"zh": "国服", "ja": "日服", "jp": "日服", "en": "美服"}


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
