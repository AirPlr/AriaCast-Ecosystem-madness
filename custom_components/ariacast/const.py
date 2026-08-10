"""Constants for the AriaCast Home Assistant integration."""

DOMAIN = "ariacast"

CONF_DB_PATH = "db_path"
CONF_HA_MODE = "ha_mode"
CONF_MANUAL_HOST = "host"
CONF_MANUAL_PORT = "port"
CONF_ADDON_URL = "addon_base_url"

DEFAULT_DB_FILENAME = "ariacast.db"
DEFAULT_STREAM_PORT = 12889

DATA_DB = "database"
DATA_NODE_MANAGER = "node_manager"
DATA_DSP = "dsp"
DATA_HUE_SYNC = "hue_sync"
DATA_PUBSUB = "pubsub"
DATA_ENTITIES = "entities"
DATA_HA_BRIDGE_SYNC = "ha_bridge_sync"
DATA_HA_ACTION_RELAY = "ha_action_relay"

SIGNAL_SPEAKER_UPDATED = f"{DOMAIN}_speaker_updated"

SERVICE_SET_ROOM_LAYOUT = "set_room_layout"
SERVICE_SET_LISTENER_POSITION = "set_listener_position"
SERVICE_SYNC_HA_AREAS = "sync_ha_areas"

PLATFORMS = ["media_player"]
