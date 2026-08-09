"""
AudioCast Server - Configuration Examples
This file shows various configuration options for different scenarios.
"""

from main import ServerConfig, AudioConfig


# ============================================================================
# Example 1: Basic Configuration (Default)
# ============================================================================
BASIC_CONFIG = ServerConfig(
    SERVER_NAME="AudioCast Speaker",
    VERSION="1.0",
    PLATFORM="Windows",
    DISCOVERY_PORT=12888,
    STREAMING_PORT=12889,
)


# ============================================================================
# Example 2: Living Room Speaker
# ============================================================================
LIVING_ROOM_CONFIG = ServerConfig(
    SERVER_NAME="Living Room Speaker",
    VERSION="1.0",
    PLATFORM="RaspberryPi",
    CODECS=["PCM"],
    DISCOVERY_PORT=12888,
    STREAMING_PORT=12889,
    AUDIO=AudioConfig(
        SAMPLE_RATE=48000,
        CHANNELS=2,
        SAMPLE_WIDTH=2,
        FRAME_DURATION_MS=20,
    ),
)


# ============================================================================
# Example 3: Bedroom Speaker
# ============================================================================
BEDROOM_CONFIG = ServerConfig(
    SERVER_NAME="Bedroom Speaker",
    VERSION="1.0",
    PLATFORM="RaspberryPi",
    DISCOVERY_PORT=12888,
    STREAMING_PORT=12889,
)


# ============================================================================
# Example 4: High-Quality Audio (Studio)
# ============================================================================
STUDIO_CONFIG = ServerConfig(
    SERVER_NAME="Studio Monitor",
    VERSION="1.0",
    PLATFORM="Linux",
    DISCOVERY_PORT=12888,
    STREAMING_PORT=12889,
    AUDIO=AudioConfig(
        SAMPLE_RATE=96000,  # Higher sample rate
        CHANNELS=2,
        SAMPLE_WIDTH=2,
        FRAME_DURATION_MS=10,  # Shorter frames for lower latency
    ),
)


# ============================================================================
# Example 5: Mono Audio (Cost-Optimized)
# ============================================================================
MONO_CONFIG = ServerConfig(
    SERVER_NAME="Budget Speaker",
    VERSION="1.0",
    PLATFORM="ESP32",
    DISCOVERY_PORT=12888,
    STREAMING_PORT=12889,
    AUDIO=AudioConfig(
        SAMPLE_RATE=16000,  # Lower sample rate
        CHANNELS=1,         # Mono instead of stereo
        SAMPLE_WIDTH=2,
        FRAME_DURATION_MS=20,
    ),
)


# ============================================================================
# Example 6: High-Latency Network (WAN/Cloud)
# ============================================================================
CLOUD_CONFIG = ServerConfig(
    SERVER_NAME="Cloud Speaker",
    VERSION="1.0",
    PLATFORM="Cloud",
    DISCOVERY_PORT=12888,
    STREAMING_PORT=12889,
    AUDIO=AudioConfig(
        SAMPLE_RATE=48000,
        CHANNELS=2,
        SAMPLE_WIDTH=2,
        FRAME_DURATION_MS=20,
    ),
)


# ============================================================================
# Configuration Selection Helper
# ============================================================================
def get_config(name: str) -> ServerConfig:
    """
    Get a configuration by name.
    
    Args:
        name: Configuration name
               - 'basic': Default configuration
               - 'living_room': Living room speaker
               - 'bedroom': Bedroom speaker
               - 'studio': High-quality audio (96kHz)
               - 'mono': Mono audio (16kHz)
               - 'cloud': High-latency network
    
    Returns:
        ServerConfig instance
    """
    configs = {
        'basic': BASIC_CONFIG,
        'living_room': LIVING_ROOM_CONFIG,
        'bedroom': BEDROOM_CONFIG,
        'studio': STUDIO_CONFIG,
        'mono': MONO_CONFIG,
        'cloud': CLOUD_CONFIG,
    }
    
    if name not in configs:
        print(f"Unknown configuration: {name}")
        print(f"Available: {', '.join(configs.keys())}")
        return BASIC_CONFIG
    
    return configs[name]


if __name__ == "__main__":
    # Print available configurations
    print("AudioCast Server - Available Configurations\n")
    
    configs_info = {
        'basic': "Default configuration",
        'living_room': "Living room speaker (RaspberryPi)",
        'bedroom': "Bedroom speaker",
        'studio': "Studio monitor (96kHz, high quality)",
        'mono': "Budget speaker (mono, 16kHz)",
        'cloud': "Cloud streaming",
    }
    
    for name, description in configs_info.items():
        config = get_config(name)
        print(f"• {name:15} - {description}")
        print(f"  Name: {config.SERVER_NAME}")
        print(f"  Port: {config.STREAMING_PORT}")
        print(f"  Audio: {config.AUDIO.SAMPLE_RATE}Hz, "
              f"{config.AUDIO.CHANNELS}ch")
        print()
