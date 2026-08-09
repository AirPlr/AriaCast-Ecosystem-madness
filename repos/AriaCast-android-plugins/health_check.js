/**
 * AriaCast Health & Socket Monitor
 * Purpose: Diagnose 'No Audio' issues by checking local capture and server sync.
 */

var statusLabel;
var detailLabel;
var isMonitoring = false;
var lastSentFrames = 0;

// --- Lifecycle Events ---

events.onServiceConnected(function(service) {
    ui.run(function() {
        setupUI();
    });
    startMonitoring();
});

events.onStateChanged(function(state) {
    ui.run(function() {
        if (statusLabel) statusLabel.setText("State: " + state);
    });
    checkHealth();
});

events.onConfigRequested(function() {
    checkHealth(); // Manual refresh
});

function setupUI() {
    ui.clear();
    // inflating the standard server item layout
    var view = ui.inflate("item_server"); 
    statusLabel = ui.findView(view, "serverName");
    detailLabel = ui.findView(view, "serverHost");
    
    if (statusLabel) statusLabel.setText("Diagnostic Tool Active");
    if (detailLabel) detailLabel.setText("Waiting for data...");
    ui.add(view);
}

function startMonitoring() {
    if (isMonitoring) return;
    isMonitoring = true;
    bg.run(function() {
        while (isMonitoring) {
            checkHealth();
            java.lang.Thread.sleep(2000); // Diagnostic poll every 2s
        }
    });
}

function checkHealth() {
    try {
        // Intercepted by App to return internal stats
        var response = ws.request("ws://localhost:12889", "get_stats", "{}", "token");
        
        if (!response || response.indexOf("Error") === 0) {
            updateUI("Ready to Test", "Start casting to begin diagnosis.");
            return;
        }

        var stats = JSON.parse(response);
        var sent = stats.sent_frames || 0;
        var received = stats.received_frames || 0;
        var buffered = stats.buffered_level || 0;
        var bitrate = stats.bitrate || "0 kbps";
        
        // Detect if frames are actually increasing
        var isCapturing = sent > lastSentFrames;
        lastSentFrames = sent;

        var healthStatus = "Health: OK ✅";
        var diagnosticMsg = "";

        if (stats.state === "OFF") {
            healthStatus = "Service Ready";
            diagnosticMsg = "Connect to a server to test data flow.";
        } 
        else if (sent === 0 && stats.state === "CASTING") {
            // Scenario: Casting UI is on, but no data is being generated
            healthStatus = "Capture Error ❌";
            diagnosticMsg = "Android is not sending audio. Check Permissions.";
        } 
        else if (!isCapturing && stats.state === "CASTING") {
            // Scenario: Stream started but then froze
            healthStatus = "Stream Stalled ⚠️";
            diagnosticMsg = "Capture active but data flow has stopped.";
        } 
        else if (received === 0 && stats.state === "CASTING") {
            // Scenario: App is sending, but server sees nothing
            healthStatus = "Network Blocked 🌐";
            diagnosticMsg = "Packets sent but 0 received. Check Firewall/VLAN.";
        } 
        else {
            // Healthy scenario
            healthStatus = "Streaming: " + bitrate;
            diagnosticMsg = "Sent: " + sent + " • Recv: " + received + " • Buf: " + buffered;
        }

        updateUI(healthStatus, diagnosticMsg);

    } catch (e) {
        updateUI("Diagnostic Error", e.toString());
    }
}

function updateUI(status, details) {
    ui.run(function() {
        if (statusLabel) statusLabel.setText(status);
        if (detailLabel) detailLabel.setText(details);
    });
}