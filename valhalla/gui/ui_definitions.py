from enum import Enum, unique

ID_JSON = "id.json"


@unique
class PluginSettingsDlgElems(str, Enum):
    # we append the user-defined name to these
    VALHALLA_HTTP_URL = "ui_valhalla_http_url"
    VALHALLA_HTTP_PARAM = "ui_valhalla_http_param"
    VALHALLA_HTTP_BLABLA = "ui_valhalla_http_secret"
    VALHALLA_GRAPH_DIRECTORY = "ui_btn_graph_folder"
    DEBUG = "debug"
    SETTINGS_SPLITTER_STATE = "settings_splitter_state"


@unique
class RouterWidgetElems(str, Enum):
    PROV_COMBO = "ui_cmb_prov"
    PROV_OPT = "ui_btn_prov_options"
    PED = "ui_btn_ped"
    BIKE = "ui_btn_bike"
    CAR = "ui_btn_car"
    TRUCK = "ui_btn_truck"
    MBIKE = "ui_btn_mbike"
    BUS = "ui_btn_bus"
    SERVER_START = "ui_btn_server_start"  # toggles start/stop with the process state
    SERVER_LOG = "ui_btn_server_log"
    SERVER_CONF = "ui_btn_server_conf"
    SERVER_INFO = "ui_btn_server_info"  # graph/version info dialog (was ui_about_btn)
    SERVER_GRAPH_EXTENT = "ui_btn_server_graph_extent"  # load graph extent (was ui_graph_btn)
    SERVER_GRAPHS_COMBO = "ui_cmb_graphs"
