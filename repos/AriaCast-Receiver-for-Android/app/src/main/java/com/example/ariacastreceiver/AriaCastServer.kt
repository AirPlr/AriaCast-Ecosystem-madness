package com.example.ariacastreceiver

import android.content.Context
import android.net.wifi.WifiManager
import android.util.Log
import io.ktor.http.*
import io.ktor.serialization.kotlinx.*
import io.ktor.serialization.kotlinx.json.*
import io.ktor.server.application.*
import io.ktor.server.engine.*
import io.ktor.server.netty.*
import io.ktor.server.plugins.contentnegotiation.*
import io.ktor.server.plugins.cors.routing.*
import io.ktor.server.request.*
import io.ktor.server.response.*
import io.ktor.server.routing.*
import io.ktor.server.websocket.*
import io.ktor.websocket.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.*
import javax.jmdns.JmDNS
import javax.jmdns.ServiceInfo
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.NetworkInterface
import java.util.*
import java.util.concurrent.ConcurrentHashMap

class AriaCastServer(
    private val context: Context,
    private var config: ServerConfig = ServerConfig()
) {
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private var server: EmbeddedServer<*, *>? = null
    private var jmdns: JmDNS? = null
    private val audioPlayer = AudioPlayer(config.audio)
    
    private val _metadata = MutableStateFlow(Metadata())
    val metadata = _metadata.asStateFlow()
    
    private val _isPlaying = MutableStateFlow(false)
    val isPlaying = _isPlaying.asStateFlow()

    private val _artworkBytes = MutableStateFlow<ByteArray?>(null)
    val artworkBytes = _artworkBytes.asStateFlow()
    
    private var lastRemoteArtworkUrl: String? = null
    
    private val metadataClients = ConcurrentHashMap.newKeySet<DefaultWebSocketServerSession>()
    private val listeningClients = ConcurrentHashMap.newKeySet<DefaultWebSocketServerSession>()
    private val controlClients = ConcurrentHashMap.newKeySet<DefaultWebSocketServerSession>()

    private val multicastLock: WifiManager.MulticastLock by lazy {
        val wifiManager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        wifiManager.createMulticastLock("AriaCastLock")
    }

    private val json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
    }

    fun updateName(newName: String) {
        config = config.copy(serverName = newName)
        restartDiscovery()
    }

    private fun restartDiscovery() {
        scope.launch(Dispatchers.IO) {
            jmdns?.unregisterAllServices()
            startDiscovery()
        }
    }

    fun start() {
        multicastLock.acquire()
        
        server = embeddedServer(Netty, port = config.streamingPort, host = "0.0.0.0") {
            install(WebSockets) {
                contentConverter = KotlinxWebsocketSerializationConverter(json)
            }
            install(ContentNegotiation) {
                json(json)
            }
            install(CORS) {
                anyHost()
                allowHeader(HttpHeaders.ContentType)
                allowMethod(HttpMethod.Options)
                allowMethod(HttpMethod.Post)
                allowMethod(HttpMethod.Get)
            }
            
            routing {
                webSocket("/audio") {
                    try {
                        sendSerialized(HandshakeResponse(
                            status = "READY",
                            sampleRate = config.audio.sampleRate,
                            channels = config.audio.channels,
                            frameSize = config.audio.frameSize
                        ))
                    } catch (e: Exception) {
                        Log.e("AriaCastServer", "Handshake failed: ${e.message}")
                    }
                    
                    try {
                        for (frame in incoming) {
                            if (frame is Frame.Binary) {
                                val data = frame.data
                                audioPlayer.enqueueFrame(data)
                                if (listeningClients.isNotEmpty()) {
                                    val binaryFrame = Frame.Binary(true, data)
                                    listeningClients.forEach { client ->
                                        scope.launch {
                                            try { client.send(binaryFrame) } catch (e: Exception) {}
                                        }
                                    }
                                }
                            }
                        }
                    } catch (e: Exception) {
                        Log.e("AriaCastServer", "Audio socket error: ${e.message}")
                    } finally {
                        audioPlayer.stop()
                    }
                }
                
                webSocket("/control") {
                    controlClients.add(this)
                    try {
                        for (frame in incoming) {
                            if (frame is Frame.Text) {
                                val text = frame.readText()
                                try {
                                    val command = json.decodeFromString<ControlMessage>(text)
                                    handleCommand(command)
                                } catch (e: Exception) {}
                            }
                        }
                    } finally {
                        controlClients.remove(this)
                    }
                }

                webSocket("/metadata") {
                    metadataClients.add(this)
                    try {
                        sendSerialized(MetadataMessage("metadata", _metadata.value))
                    } catch (e: Exception) {}
                    
                    try {
                        for (frame in incoming) {
                            if (frame is Frame.Text) {
                                val text = frame.readText()
                                try {
                                    val root = json.parseToJsonElement(text).jsonObject
                                    if (root["type"]?.jsonPrimitive?.content == "update") {
                                        root["data"]?.jsonObject?.let { updateMetadataFromJson(it) }
                                    }
                                } catch (e: Exception) {}
                            }
                        }
                    } finally {
                        metadataClients.remove(this)
                    }
                }

                webSocket("/listen") {
                    listeningClients.add(this)
                    try {
                        sendSerialized(mapOf(
                            "sample_rate" to config.audio.sampleRate,
                            "channels" to config.audio.channels
                        ))
                        for (frame in incoming) { }
                    } finally {
                        listeningClients.remove(this)
                    }
                }

                get("/artwork") {
                    _artworkBytes.value?.let {
                        call.respondBytes(it, ContentType.Image.JPEG)
                    } ?: call.respond(HttpStatusCode.NotFound)
                }

                get("/image/artwork") {
                    _artworkBytes.value?.let {
                        call.respondBytes(it, ContentType.Image.JPEG)
                    } ?: call.respond(HttpStatusCode.NotFound)
                }

                post("/metadata") {
                    try {
                        val body = call.receive<JsonObject>()
                        val data = if (body.containsKey("data")) body["data"]?.jsonObject else body
                        data?.let { updateMetadataFromJson(it) }
                        call.respond(mapOf("success" to true))
                    } catch (e: Exception) {
                        call.respond(HttpStatusCode.BadRequest, mapOf("error" to e.message))
                    }
                }
                
                get("/api/metadata") {
                    call.respond(_metadata.value)
                }

                post("/api/command") {
                    try {
                        val cmd = call.receive<ControlMessage>()
                        handleCommand(cmd)
                        call.respond(mapOf("success" to true))
                    } catch (e: Exception) {
                        call.respond(HttpStatusCode.BadRequest, mapOf("error" to e.message))
                    }
                }
            }
        }.start(wait = false)
        
        startDiscovery()
        startUdpDiscovery()
    }

    fun handleCommand(cmd: ControlMessage) {
        val action = cmd.action ?: cmd.command
        when (action) {
            "play" -> {
                _isPlaying.value = true
                updateMetadata(_metadata.value.copy(isPlaying = true), false)
            }
            "pause" -> {
                _isPlaying.value = false
                updateMetadata(_metadata.value.copy(isPlaying = false), false)
            }
            "play_pause" -> {
                val newState = !_isPlaying.value
                _isPlaying.value = newState
                updateMetadata(_metadata.value.copy(isPlaying = newState), false)
            }
            "stop" -> {
                _isPlaying.value = false
                audioPlayer.stop()
                updateMetadata(_metadata.value.copy(isPlaying = false), false)
            }
        }
        
        scope.launch {
            controlClients.forEach { client ->
                try { client.sendSerialized(cmd) } catch (e: Exception) {}
            }
        }
    }

    private fun updateMetadataFromJson(data: JsonObject) {
        val current = _metadata.value
        var songChanged = false
        
        val newTitle = data["title"]?.jsonPrimitive?.contentOrNull
        if (newTitle != null && newTitle != current.title) {
            songChanged = true
        }
        
        val updated = current.copy(
            title = newTitle ?: current.title,
            artist = data["artist"]?.jsonPrimitive?.contentOrNull ?: current.artist,
            album = data["album"]?.jsonPrimitive?.contentOrNull ?: current.album,
            artworkUrl = data["artwork_url"]?.jsonPrimitive?.contentOrNull 
                ?: data["artworkUrl"]?.jsonPrimitive?.contentOrNull 
                ?: current.artworkUrl,
            durationMs = data["duration_ms"]?.jsonPrimitive?.longOrNull 
                ?: data["durationMs"]?.jsonPrimitive?.longOrNull 
                ?: current.durationMs,
            positionMs = data["position_ms"]?.jsonPrimitive?.longOrNull 
                ?: data["positionMs"]?.jsonPrimitive?.longOrNull 
                ?: current.positionMs,
            isPlaying = data["is_playing"]?.jsonPrimitive?.booleanOrNull 
                ?: data["isPlaying"]?.jsonPrimitive?.booleanOrNull 
                ?: current.isPlaying
        )
        
        updateMetadata(updated, songChanged)
    }

    private fun updateMetadata(meta: Metadata, songChanged: Boolean) {
        _metadata.value = meta
        _isPlaying.value = meta.isPlaying
        
        val newUrl = meta.artworkUrl
        if (newUrl != null && (newUrl != lastRemoteArtworkUrl || songChanged) && newUrl.startsWith("http")) {
            lastRemoteArtworkUrl = newUrl
            downloadArtwork(newUrl)
        }
        
        scope.launch {
            val msg = MetadataMessage("metadata", meta)
            metadataClients.forEach { client ->
                try { client.sendSerialized(msg) } catch (e: Exception) {}
            }
        }
    }

    private fun downloadArtwork(url: String) {
        scope.launch {
            try {
                val connection = java.net.URL(url).openConnection() as HttpURLConnection
                connection.setRequestProperty("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
                connection.connectTimeout = 5000
                connection.readTimeout = 5000
                
                if (connection.responseCode == HttpURLConnection.HTTP_OK) {
                    val bytes = connection.inputStream.readBytes()
                    _artworkBytes.value = bytes
                }
                connection.disconnect()
            } catch (e: Exception) {
                Log.e("AriaCastServer", "Failed to download artwork: ${e.message}")
            }
        }
    }

    private fun startDiscovery() {
        scope.launch(Dispatchers.IO) {
            try {
                val ip = getLocalIpAddress()
                jmdns = JmDNS.create(InetAddress.getByName(ip))
                val serviceInfo = ServiceInfo.create(
                    "_audiocast._tcp.local.",
                    "${config.serverName}._audiocast._tcp.local.",
                    config.streamingPort,
                    0, 0,
                    mapOf(
                        "version" to "1.0",
                        "samplerate" to config.audio.sampleRate.toString(),
                        "channels" to config.audio.channels.toString()
                    )
                )
                jmdns?.registerService(serviceInfo)
            } catch (e: Exception) {
                Log.e("AriaCastServer", "mDNS error: ${e.message}")
            }
        }
    }

    private fun startUdpDiscovery() {
        scope.launch(Dispatchers.IO) {
            try {
                val socket = DatagramSocket(config.discoveryPort)
                socket.broadcast = true
                val buffer = ByteArray(1024)
                while (isActive) {
                    val packet = DatagramPacket(buffer, buffer.size)
                    try {
                        socket.receive(packet)
                        val data = String(packet.data, 0, packet.length).trim()
                        if (data == "DISCOVER_AUDIOCAST") {
                            val response = """
                                {
                                    "server_name": "${config.serverName}",
                                    "ip": "${getLocalIpAddress()}",
                                    "port": ${config.streamingPort},
                                    "samplerate": ${config.audio.sampleRate},
                                    "channels": ${config.audio.channels}
                                }
                            """.trimIndent()
                            val respBytes = response.toByteArray()
                            val respPacket = DatagramPacket(respBytes, respBytes.size, packet.address, packet.port)
                            socket.send(respPacket)
                        }
                    } catch (e: Exception) {
                        if (!isActive) break
                    }
                }
            } catch (e: Exception) {
                Log.e("AriaCastServer", "UDP Discovery error: ${e.message}")
            }
        }
    }

    private fun getLocalIpAddress(): String {
        try {
            val interfaces = NetworkInterface.getNetworkInterfaces()
            while (interfaces.hasMoreElements()) {
                val networkInterface = interfaces.nextElement()
                if (networkInterface.isLoopback || !networkInterface.isUp) continue
                
                val addresses = networkInterface.inetAddresses
                while (addresses.hasMoreElements()) {
                    val address = addresses.nextElement()
                    if (!address.isLoopbackAddress && address.address.size == 4) {
                        return address.hostAddress ?: "127.0.0.1"
                    }
                }
            }
        } catch (e: Exception) {}
        return "127.0.0.1"
    }

    fun stop() {
        if (multicastLock.isHeld) {
            multicastLock.release()
        }
        server?.stop(1000, 1000)
        jmdns?.unregisterAllServices()
        jmdns?.close()
        audioPlayer.stop()
        scope.cancel()
    }
}
