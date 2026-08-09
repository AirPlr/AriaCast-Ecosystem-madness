package main

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

// ============================================================================
// 1. Configuration & Models
// ============================================================================

type AudioConfig struct {
	SampleRate      int `json:"sample_rate"`
	Channels        int `json:"channels"`
	SampleWidth     int `json:"sample_width"`
	FrameDurationMs int `json:"frame_duration_ms"`
}

func (a AudioConfig) FrameSize() int {
	return a.SampleRate * a.Channels * a.SampleWidth * a.FrameDurationMs / 1000
}

type ServerConfig struct {
	ServerName    string      `json:"server_name"`
	StreamingPort int         `json:"streaming_port"`
	DiscoveryPort int         `json:"discovery_port"`
	Audio         AudioConfig `json:"audio"`
}

type Metadata struct {
	Title      string `json:"title"`
	Artist     string `json:"artist"`
	Album      string `json:"album"`
	ArtworkURL string `json:"artwork_url"`
	DurationMs int    `json:"duration_ms"`
	PositionMs int    `json:"position_ms"`
	IsPlaying  bool   `json:"is_playing"`
}

// ============================================================================
// 2. The Server Core
// ============================================================================

type AriaServer struct {
	Config      *ServerConfig
	
	// Metadata & Artwork (Strict Mutex)
	metaMu       sync.Mutex 
	currentMeta  Metadata
	artworkBytes []byte
	metaClients  map[*websocket.Conn]bool

	// Audio State
	audioMu     sync.Mutex
	audioClient *websocket.Conn
	
	// Control State
	controlMu     sync.Mutex
	controlClient *websocket.Conn

	// Web/Browser Broadcast Pool
	poolMu      sync.RWMutex
	pool        map[chan []byte]bool

	// Pipe Bridge State
	pipePath    string
	pipeChan    chan []byte
	pipePaused  int32  // Atomic: 0=playing, 1=paused

	receivedCount uint64
}

func NewAriaServer(pipePath string) *AriaServer {
	return &AriaServer{
		Config: &ServerConfig{
			ServerName:    "MusicAssistant AriaCast Receiver",
			StreamingPort: 12889,
			DiscoveryPort: 12888,
			Audio: AudioConfig{
				SampleRate: 48000, Channels: 2, SampleWidth: 2, FrameDurationMs: 20,
			},
		},
		metaClients: make(map[*websocket.Conn]bool),
		pool:        make(map[chan []byte]bool),
		pipePath:    pipePath,
		pipeChan:    make(chan []byte, 100),
	}
}

// ============================================================================
// 3. UDP Discovery
// ============================================================================

func (s *AriaServer) StartDiscovery() {
	addr, _ := net.ResolveUDPAddr("udp4", fmt.Sprintf("0.0.0.0:%d", s.Config.DiscoveryPort))
	conn, err := net.ListenUDP("udp4", addr)
	if err != nil {
		log.Fatalf("FATAL: Cannot bind UDP discovery port: %v", err)
	}
	defer conn.Close()

	log.Printf("✅ Discovery Service Active on UDP :%d", s.Config.DiscoveryPort)
	buf := make([]byte, 1024)

	for {
		n, remote, err := conn.ReadFromUDP(buf)
		if err != nil { continue }
		data := bytes.TrimSpace(buf[:n])
		
		if string(data) == "DISCOVER_AUDIOCAST" {
			response := map[string]interface{}{
				"server_name": s.Config.ServerName,
				"ip":          s.getOutboundIP(),
				"port":        s.Config.StreamingPort,
				"samplerate":  s.Config.Audio.SampleRate,
				"channels":    s.Config.Audio.Channels,
			}
			respBytes, _ := json.Marshal(response)
			conn.WriteToUDP(respBytes, remote)
		}
	}
}

func (s *AriaServer) getOutboundIP() string {
	conn, err := net.Dial("udp", "8.8.8.8:80")
	if err != nil { return "127.0.0.1" }
	defer conn.Close()
	return conn.LocalAddr().(*net.UDPAddr).IP.String()
}

// ============================================================================
// 4. Pipe Writer
// ============================================================================

func (s *AriaServer) StartPipeBridge() {
	if s.pipePath == "" { return }
	
	log.Printf("🪈 Pipe Bridge Enabled: %s", s.pipePath)

	// Create a silent frame (all zeros)
	silentFrame := make([]byte, 3840)  // 20ms of silence

	for {
		log.Printf("⏳ Waiting for Music Assistant to read pipe...")
		f, err := os.OpenFile(s.pipePath, os.O_WRONLY, 0666)
		if err != nil {
			time.Sleep(2 * time.Second)
			continue
		}

		log.Printf("🚀 Pipe Connected!")
		
		for frame := range s.pipeChan {
			// Check if paused - if so, write silence instead of actual audio
			var dataToWrite []byte
			if atomic.LoadInt32(&s.pipePaused) == 1 {
				dataToWrite = silentFrame  // Write silence when paused
			} else {
				dataToWrite = frame  // Write actual audio when playing
			}
			
			if _, err := f.Write(dataToWrite); err != nil {
				log.Printf("⚠️ Pipe closed: %v", err)
				break
			}
		}

		f.Close()
		log.Printf("🛑 Pipe Disconnected. Resetting...")
		drainLoop:
		for {
			select {
			case <-s.pipeChan:
			default:
				break drainLoop
			}
		}
	}
}

// ============================================================================
// 5. Audio Streaming Handler
// ============================================================================

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

func (s *AriaServer) HandleAudio(w http.ResponseWriter, r *http.Request) {
	s.audioMu.Lock()
	if s.audioClient != nil {
		s.audioMu.Unlock()
		http.Error(w, "Busy", http.StatusForbidden)
		return
	}
	
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		s.audioMu.Unlock()
		return
	}
	
	s.audioClient = conn
	s.audioMu.Unlock()
	log.Printf("🎧 Audio Source Connected: %s", r.RemoteAddr)

	defer func() {
		s.audioMu.Lock()
		s.audioClient = nil
		s.audioMu.Unlock()
		conn.Close()
		log.Printf("🔌 Audio Source Disconnected")
	}()

	handshake := map[string]interface{}{
		"status":      "READY",
		"sample_rate": s.Config.Audio.SampleRate,
		"channels":    s.Config.Audio.Channels,
		"frame_size":  s.Config.Audio.FrameSize(),
	}
	if err := conn.WriteJSON(handshake); err != nil { return }

	for {
		mt, msg, err := conn.ReadMessage()
		if err != nil { break }
		if mt == websocket.BinaryMessage {
			atomic.AddUint64(&s.receivedCount, 1)
			s.broadcastToPool(msg)
			if s.pipePath != "" {
				select {
				case s.pipeChan <- msg:
				default:
				}
			}
		}
	}
}

// ============================================================================
// 6. Control Handler (API & WebSocket)
// ============================================================================

func (s *AriaServer) HandleControl(w http.ResponseWriter, r *http.Request) {
	s.controlMu.Lock()
	if s.controlClient != nil {
		s.controlMu.Unlock()
		http.Error(w, "Busy", http.StatusForbidden)
		return
	}
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		s.controlMu.Unlock()
		return
	}
	s.controlClient = conn
	s.controlMu.Unlock()
	
	log.Printf("🎮 Control Client Connected: %s", r.RemoteAddr)

	defer func() {
		s.controlMu.Lock()
		s.controlClient = nil
		s.controlMu.Unlock()
		conn.Close()
		log.Printf("🎮 Control Client Disconnected")
	}()

	for {
		if _, _, err := conn.NextReader(); err != nil { break }
	}
}

func (s *AriaServer) HandleAPICommand(w http.ResponseWriter, r *http.Request) {
	// Enable CORS for all
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}

	if r.Method != http.MethodPost {
		http.Error(w, "POST only", 405)
		return
	}

	var req map[string]string
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid JSON", 400)
		return
	}

	action := req["action"]
	if action == "" {
		http.Error(w, "Missing action", 400)
		return
	}

	s.controlMu.Lock()
	defer s.controlMu.Unlock()
	
	if s.controlClient == nil {
		log.Printf("⚠️ No control client connected for command '%s'", action)
		http.Error(w, "No control client connected", 503)
		return
	}

	command := map[string]string{"action": action}
	if err := s.controlClient.WriteJSON(command); err != nil {
		log.Printf("❌ Failed to send command: %v", err)
		http.Error(w, "Failed to send", 500)
		return
	}
	
	// Update metadata state and pipe pause state
	if action == "play" || action == "pause" {
		newState := (action == "play")
		
		// Update pipe pause state atomically
		if newState {
			atomic.StoreInt32(&s.pipePaused, 0)  // Resume pipe writes
		} else {
			atomic.StoreInt32(&s.pipePaused, 1)  // Pause pipe writes
		}
		
		// Broadcast metadata update
		s.metaMu.Lock()
		s.currentMeta.IsPlaying = newState
		msg := map[string]interface{}{"type": "metadata", "data": s.currentMeta}
		for client := range s.metaClients {
			client.WriteJSON(msg)
		}
		s.metaMu.Unlock()
		log.Printf("📤 Sent Control Command: %s (is_playing=%v, pipe_paused=%d)", action, newState, atomic.LoadInt32(&s.pipePaused))
	} else {
		log.Printf("📤 Sent Control Command: %s", action)
	}
	
	w.WriteHeader(http.StatusOK)
}

// ============================================================================
// 7. Metadata Handler & Artwork
// ============================================================================

func (s *AriaServer) HandleMetadata(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Upgrade") == "websocket" {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil { return }

		s.metaMu.Lock()
		s.metaClients[conn] = true
		// Send initial state safely
		err = conn.WriteJSON(map[string]interface{}{"type": "metadata", "data": s.currentMeta})
		s.metaMu.Unlock()

		if err != nil {
			conn.Close()
			return
		}

		defer func() {
			s.metaMu.Lock()
			delete(s.metaClients, conn)
			s.metaMu.Unlock()
			conn.Close()
		}()

		for {
			var msg map[string]interface{}
			if err := conn.ReadJSON(&msg); err != nil { break }
			if msg["type"] == "update" {
				if data, ok := msg["data"].(map[string]interface{}); ok {
					s.updateMetadata(data)
				}
			}
		}
		return
	}

	if r.Method == http.MethodPost {
		var body map[string]interface{}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, "Invalid JSON", 400)
			return
		}
		data := body
		if wrapped, ok := body["data"].(map[string]interface{}); ok { data = wrapped }
		s.updateMetadata(data)
		w.WriteHeader(http.StatusOK)
		return
	}
	http.Error(w, "Method Not Allowed", 405)
}

func getInt(v interface{}) int {
	if v == nil { return 0 }
	switch val := v.(type) {
	case float64: return int(val)
	case int: return val
	case int64: return int(val)
	default: return 0
	}
}

func (s *AriaServer) updateMetadata(data map[string]interface{}) {
	s.metaMu.Lock()
	defer s.metaMu.Unlock()
	
	if v, ok := data["title"].(string); ok { s.currentMeta.Title = v }
	if v, ok := data["artist"].(string); ok { s.currentMeta.Artist = v }
	if v, ok := data["album"].(string); ok { s.currentMeta.Album = v }
	
	if v, ok := data["is_playing"].(bool); ok { s.currentMeta.IsPlaying = v }
	if v, ok := data["isPlaying"].(bool); ok { s.currentMeta.IsPlaying = v }

	if v, ok := data["durationMs"]; ok { s.currentMeta.DurationMs = getInt(v) }
	
	if v, ok := data["positionMs"]; ok { s.currentMeta.PositionMs = getInt(v) }

	var newUrl string
	if v, ok := data["artworkUrl"].(string); ok { newUrl = v }
	
	if newUrl != "" && newUrl != s.currentMeta.ArtworkURL {
		s.currentMeta.ArtworkURL = newUrl
		go s.downloadArtwork(newUrl)
	}
	
	msg := map[string]interface{}{"type": "metadata", "data": s.currentMeta}
	for client := range s.metaClients {
		client.WriteJSON(msg)
	}
}

func (s *AriaServer) downloadArtwork(url string) {
	client := http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get(url)
	if err != nil || resp.StatusCode != 200 { return }
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	
	// We only need the lock to set the bytes
	// It's separate from updateMetadata's lock, which is released by now (via goroutine or defer)
	// But updateMetadata calls this in a goroutine, so it's safe.
	// HOWEVER: If we use the SAME mutex s.metaMu, we must be careful.
	// updateMetadata spawns a goroutine for this, so it's fine.
	s.metaMu.Lock()
	s.artworkBytes = data
	s.metaMu.Unlock()
}

// ============================================================================
// 8. Web Dashboard
// ============================================================================

func (s *AriaServer) broadcastToPool(data []byte) {
	s.poolMu.RLock()
	defer s.poolMu.RUnlock()
	for ch := range s.pool {
		select {
		case ch <- data:
		default:
		}
	}
}

func (s *AriaServer) ServeWeb(port int) {
	mux := http.NewServeMux()
	
	// 1. WAV Stream
	mux.HandleFunc("/stream.wav", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "audio/wav")
		w.Write(s.wavHeader())
		ch := make(chan []byte, 50)
		s.poolMu.Lock()
		s.pool[ch] = true
		s.poolMu.Unlock()
		defer func() {
			s.poolMu.Lock()
			delete(s.pool, ch)
			s.poolMu.Unlock()
		}()
		for frame := range ch {
			if _, err := w.Write(frame); err != nil { return }
			if f, ok := w.(http.Flusher); ok { f.Flush() }
		}
	})

	// 2. Artwork Proxy
	mux.HandleFunc("/artwork", func(w http.ResponseWriter, r *http.Request) {
		s.metaMu.Lock()
		data := s.artworkBytes
		s.metaMu.Unlock()
		if len(data) == 0 {
			http.Error(w, "No artwork", 404)
			return
		}
		w.Header().Set("Content-Type", "image/jpeg")
		w.Write(data)
	})

	// 3. API Command (FIX: Registered here so port 8080 knows it)
	mux.HandleFunc("/api/command", s.HandleAPICommand)

	// 4. Dashboard HTML
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		html := `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>AriaCast</title><style>body{margin:0;padding:0;background:#121212;color:#e0e0e0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh}.player{background:#1e1e1e;padding:2rem;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,0.5);width:350px;text-align:center}.artwork{width:280px;height:280px;border-radius:12px;object-fit:cover;background:#333;margin-bottom:1.5rem}h2{margin:0;font-size:1.25rem;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}h3{margin:0.5rem 0 0;font-size:1rem;color:#b3b3b3}.controls{display:flex;justify-content:center;gap:1.5rem;margin:1.5rem 0}.btn{background:none;border:none;color:#fff;font-size:1.5rem;cursor:pointer;transition:transform 0.1s}.btn:active{transform:scale(0.95)}.progress-container{margin-top:1.5rem;display:flex;align-items:center;justify-content:space-between;font-size:0.8rem;color:#888}.bar-bg{flex-grow:1;height:4px;background:#333;border-radius:2px;margin:0 10px;position:relative}.bar-fill{height:100%;background:#1db954;border-radius:2px;width:0%;transition:width 0.1s linear}audio{width:100%;margin-top:1.5rem;outline:none}</style></head><body><div class="player"><div style="color:#1db954;font-size:0.7rem;margin-bottom:1rem;font-weight:bold">ARIACAST CONNECTED</div><img id="art" class="artwork" src="" alt=""><h2 id="title">Waiting for music...</h2><h3 id="artist">Ready to cast</h3><div class="controls"><button class="btn" onclick="send('previous')">⏮</button><button class="btn" onclick="send('play')">▶</button><button class="btn" onclick="send('pause')">⏸</button><button class="btn" onclick="send('next')">⏭</button></div><div class="progress-container"><span id="currTime">0:00</span><div class="bar-bg"><div id="bar" class="bar-fill"></div></div><span id="totTime">0:00</span></div><audio controls autoplay src="/stream.wav"></audio></div><script>const art=document.getElementById('art');const title=document.getElementById('title');const artist=document.getElementById('artist');const bar=document.getElementById('bar');const currTime=document.getElementById('currTime');const totTime=document.getElementById('totTime');let duration=0,position=0,isPlaying=false,lastUpdate=Date.now();function fmtTime(ms){if(!ms||ms<0)return "0:00";const s=Math.floor(ms/1000),m=Math.floor(s/60),rs=s%60;return m+":"+(rs<10?"0":"")+rs}const ws=new WebSocket("ws://"+location.hostname+":12889/metadata");ws.onmessage=(e)=>{const msg=JSON.parse(e.data);if(msg.type==="metadata"){const d=msg.data;title.innerText=d.title||"Unknown Title";artist.innerText=d.artist||"Unknown Artist";if(d.artwork_url)art.src="/artwork?t="+Date.now();duration=d.duration_ms||0;position=d.position_ms||0;isPlaying=d.is_playing;lastUpdate=Date.now();totTime.innerText=fmtTime(duration);updateProgress()}};function send(action){fetch('/api/command',{method:'POST',body:JSON.stringify({action})})}setInterval(()=>{if(isPlaying){const now=Date.now();position+=(now-lastUpdate);lastUpdate=now}else{lastUpdate=Date.now()}updateProgress()},250);function updateProgress(){if(position>duration)position=duration;currTime.innerText=fmtTime(position);const pct=duration>0?(position/duration)*100:0;bar.style.width=pct+"%"}</script></body></html>`
		w.Write([]byte(html))
	})

	log.Printf("🌐 Web Dashboard running at http://0.0.0.0:%d", port)
	http.ListenAndServe(fmt.Sprintf(":%d", port), mux)
}

func (s *AriaServer) wavHeader() []byte {
	h := make([]byte, 44)
	copy(h[0:4], "RIFF")
	binary.LittleEndian.PutUint32(h[4:8], 0xFFFFFFFF)
	copy(h[8:12], "WAVE")
	copy(h[12:16], "fmt ")
	binary.LittleEndian.PutUint32(h[16:20], 16)
	binary.LittleEndian.PutUint16(h[20:22], 1)
	binary.LittleEndian.PutUint16(h[22:24], uint16(s.Config.Audio.Channels))
	binary.LittleEndian.PutUint32(h[24:28], uint32(s.Config.Audio.SampleRate))
	binary.LittleEndian.PutUint32(h[28:32], uint32(s.Config.Audio.SampleRate*s.Config.Audio.Channels*2))
	binary.LittleEndian.PutUint16(h[32:34], uint16(s.Config.Audio.Channels*2))
	binary.LittleEndian.PutUint16(h[34:36], 16)
	copy(h[36:40], "data")
	binary.LittleEndian.PutUint32(h[40:44], 0xFFFFFFFF)
	return h
}

// ============================================================================
// 9. Main Entry
// ============================================================================

func main() {
	webMode := flag.Bool("web", false, "Enable web interface on port 8080")
	pipePath := flag.String("pipe", "", "Path to named pipe for audio output")
	flag.Parse()

	server := NewAriaServer(*pipePath)
	
	go server.StartDiscovery()
	go server.StartPipeBridge()
	
	if *webMode {
		go server.ServeWeb(8080)
	}

	muxMain := http.NewServeMux()
	muxMain.HandleFunc("/audio", server.HandleAudio)
	muxMain.HandleFunc("/metadata", server.HandleMetadata)
	muxMain.HandleFunc("/control", server.HandleControl) 
	muxMain.HandleFunc("/api/command", server.HandleAPICommand)
	
	// Artwork endpoint for Music Assistant integration
	muxMain.HandleFunc("/image/artwork", func(w http.ResponseWriter, r *http.Request) {
		server.metaMu.Lock()
		data := server.artworkBytes
		server.metaMu.Unlock()
		if len(data) == 0 {
			http.Error(w, "No artwork", 404)
			return
		}
		w.Header().Set("Content-Type", "image/jpeg")
		w.Write(data)
	})
	
	addr := fmt.Sprintf(":%d", server.Config.StreamingPort)
	log.Printf("🚀 AriaCast Server Ready on %s", addr)
	http.ListenAndServe(addr, muxMain)
}