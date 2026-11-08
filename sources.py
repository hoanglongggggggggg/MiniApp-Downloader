ADAPTERS = {
    "duolai": {
        "host": "xiaoguofanxing.lizhibj.cn",
        "base": "/api/duolai",
        "list":     "/video/list",
        "browse":   "/video/recommend",
        "banner":   "/recommend/banner",
        "playlist": "/video/play_list",
        "playurl":  "/video/play_url",
        "detail":   "/video/def",
        "public_browse": True,
        "signed": "l_sign",
    },
}

WIP_KWAI = ("App Kwai chưa có app Douyin chủ hộ nên phải dùng token của app Douyin khác. "
            "Bộ nào cũng có ở app Douyin thì xem/tải bình thường; bộ chỉ có riêng bên "
            "Kwai sẽ trả 0 tập.")

SOURCES = [
    {"name": "多来看点",   "app_id": "tt5b4d2df0c47bdd2201", "adapter": "duolai",   "status": "builtin"},
    {"name": "黑岩漫故事", "app_id": "tt839df5201ca3d79a01", "adapter": "duolai",   "status": "builtin"},
    {"name": "金猫漫品",   "app_id": "tt3eca4877a1fed32a01", "adapter": "duolai",   "status": "builtin"},
    {"name": "万花故事会", "app_id": "ttbe17c8aa2121c82101", "adapter": "duolai",   "status": "builtin"},

    {"name": "小七漫爽阁", "app_id": "tt48293e94d71caebe01", "adapter": "duolai", "status": "custom"},
    {"name": "星月漫故事", "app_id": "tte1e6975b7eecc91e01", "adapter": "duolai", "status": "custom"},
    {"name": "萌漫时空",   "app_id": "tte06596d87f5402a101", "adapter": "duolai", "status": "custom"},
    {"name": "黑岩漫阅读", "app_id": "ttda9fac22459174db01", "adapter": "duolai", "status": "custom"},
    {"name": "黑岩漫故事(HEIY)", "app_id": "tt18b26090c29ed0ca01", "adapter": "duolai", "status": "custom"},
    {"name": "萌虎漫品",   "app_id": "tte8649ed977500ed601", "adapter": "duolai", "status": "custom"},
    {"name": "星语超前点播",   "app_id": "ttea779cca741307d601", "adapter": "duolai", "status": "custom"},

    {"name": "多来看点(Kwai)",   "app_id": "ks700309742348504087", "adapter": "duolai", "status": "kwai_partial", "wip": WIP_KWAI},
    {"name": "多看点",           "app_id": "ks712976116607639441", "adapter": "duolai", "status": "custom"},
    {"name": "小七漫爽阁(Kwai)", "app_id": "ks659777347983836430", "adapter": "duolai", "status": "kwai_partial", "wip": WIP_KWAI},
    {"name": "大奉漫故事",       "app_id": "ks666814220347126171", "adapter": "duolai", "status": "kwai_partial", "wip": WIP_KWAI},
    {"name": "黑岩故事会",       "app_id": "ks711287268160189724", "adapter": "duolai", "status": "kwai_partial", "wip": WIP_KWAI},
    {"name": "星光漫故事",       "app_id": "ks674695520187268463", "adapter": "duolai", "status": "custom"},
    {"name": "声动映画",         "app_id": "ks675258470852013744", "adapter": "duolai", "status": "custom"},
    {"name": "无界看点",         "app_id": "ks687080419825996245", "adapter": "duolai", "status": "kwai_partial", "wip": WIP_KWAI},
    {"name": "漫影像",           "app_id": "ks654710799153819764", "adapter": "duolai", "status": "kwai_partial", "wip": WIP_KWAI},
]


AUTH_REALM = {
    "tt3eca4877a1fed32a01": "tt5b4d2df0c47bdd2201",
    "tt48293e94d71caebe01": "tt5b4d2df0c47bdd2201",
    "ks712976116607639441": "tt5b4d2df0c47bdd2201",
    "ks675258470852013744": "tt5b4d2df0c47bdd2201",
    "ks674695520187268463": "tt5b4d2df0c47bdd2201",
    "ttda9fac22459174db01": "tt839df5201ca3d79a01",
}

APP_VERSION = {
    "tt5b4d2df0c47bdd2201": "3.9.39",
    "tt3eca4877a1fed32a01": "3.9.39",
    "tt48293e94d71caebe01": "3.9.39",
    "ks712976116607639441": "3.9.40",
    "ks675258470852013744": "3.9.40",
    "ks674695520187268463": "3.9.40",
    "tt839df5201ca3d79a01": "3.9.41",
    "ttda9fac22459174db01": "3.9.33",
    "ttbe17c8aa2121c82101": "3.9.41",
    "tte06596d87f5402a101": "3.9.41",
}


def auth_app(app_id):
    return AUTH_REALM.get(app_id, app_id)


def app_version(app_id, default="1"):
    return APP_VERSION.get(app_id, default)
