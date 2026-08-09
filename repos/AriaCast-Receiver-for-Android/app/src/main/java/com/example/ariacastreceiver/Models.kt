package com.example.ariacastreceiver

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class AudioConfig(
    @SerialName("sample_rate") val sampleRate: Int = 48000,
    @SerialName("channels") val channels: Int = 2,
    @SerialName("sample_width") val sampleWidth: Int = 2,
    @SerialName("frame_duration_ms") val frameDurationMs: Int = 20
) {
    val frameSize: Int get() = sampleRate * channels * sampleWidth * frameDurationMs / 1000
}

@Serializable
data class ServerConfig(
    @SerialName("server_name") val serverName: String = "AriaCast Android",
    @SerialName("streaming_port") val streamingPort: Int = 12889,
    @SerialName("discovery_port") val discoveryPort: Int = 12888,
    val audio: AudioConfig = AudioConfig()
)

@Serializable
data class Metadata(
    val title: String? = null,
    val artist: String? = null,
    val album: String? = null,
    @SerialName("artwork_url") val artworkUrl: String? = null,
    @SerialName("duration_ms") val durationMs: Long = 0,
    @SerialName("position_ms") val positionMs: Long = 0,
    @SerialName("is_playing") val isPlaying: Boolean = false
)

@Serializable
data class HandshakeResponse(
    val status: String,
    @SerialName("sample_rate") val sampleRate: Int,
    val channels: Int,
    @SerialName("frame_size") val frameSize: Int
)

@Serializable
data class MetadataMessage(
    val type: String,
    val data: Metadata
)

@Serializable
data class ControlMessage(
    val command: String? = null,
    val action: String? = null,
    val value: Int? = null,
    @SerialName("position_ms") val positionMs: Long? = null
)
