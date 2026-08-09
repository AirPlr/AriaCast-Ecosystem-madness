## AriaCast Plugin System Documentation

The AriaCast plugin architecture allows developers to extend the application's functionality by executing custom JavaScript code. The system relies on the Mozilla Rhino engine to interpret JavaScript and provides a bridge to native Android components. Plugins can generate native user interfaces, communicate with external network services, and run background tasks without modifying the core application code.

### 1\. Plugin Architecture and Structure

To function correctly, every plugin requires two essential files:

- **Manifest File (.json):** This file defines the core metadata of the plugin. It is parsed to configure the plugin within the application and must include the following string fields: id, name, description, version, author, and scriptPath.  

- **Logic File (.js):** This is the JavaScript file referenced by the scriptPath in the JSON manifest. It contains the operational logic of the plugin.  

**Installation Sources:** Plugins can be built directly into the application's internal storage (filesDir/plugins), as seen with the pre-installed "MusicAssistant Control" and "Plugin SDK Showcase" plugins. Alternatively, users can load custom plugins by selecting an external folder via Android's Scoped Storage capabilities (DocumentTree URI).

### 2\. JavaScript API Reference

The AriaCast PluginManager injects several helper objects directly into the JavaScript execution context, allowing scripts to interact seamlessly with Android native features.

#### UI Management (ui)

The ui object is used to construct and manipulate native Android views safely.

- **ui.run(runnable):** Forces the provided function to execute on the main Android UI thread. This is mandatory for any view manipulation.  

- **ui.clear():** Completely removes all views from the plugin's dedicated sub-container (LinearLayout) and sets its visibility to GONE.  

- **ui.inflate(layoutName):** Instantiates a native Android layout XML file by its string name and returns the View object.  

- **ui.findView(parentView, viewIdName):** Searches for a specific child view within a parent view using the view's ID string name.  

- **ui.add(view):** Appends a created view to the plugin's isolated sub-container and ensures the container is visible on the screen.  

#### Background Execution (bg)

- **bg.run(runnable):** Spawns a new background thread to execute the provided function. This is critical for preventing heavy calculations, loops, or network requests from freezing the application's interface. Any exceptions thrown in this background thread are safely caught and logged.  

#### Persistent Storage (storage)

The storage helper provides key-value persistence using Android's SharedPreferences. Keys are automatically prefixed with the plugin's id (plugin_{id}\_{key}) to prevent data collisions between different plugins.

- **storage.get(key):** Retrieves a stored string value.  

- **storage.set(key, value):** Saves a string value to persistent storage.  

#### Event Listeners (events)

Plugins react to application lifecycle and service events using the events object.

- **events.onServiceConnected(callback):** Triggered when the AudioCastService successfully connects. The callback receives the service object as an argument.  

- **events.onStateChanged(callback):** Triggered whenever the streaming state of the service changes. The callback receives the name of the new state as an argument.  

- **events.onConfigRequested(callback):** Triggered when a user calls the plugin's detail dialog within the application's Plugin UI.  

#### Network Operations (http, ws)

- **http.fetch(url, headersJson):** Executes a synchronous HTTP GET request with a 5-second timeout. Custom headers can be passed as a JSON string.  

- **http.post(url, body, headersJson):** Executes a synchronous HTTP POST request, sending the body string. It forces the Content-Type to application/json and applies a 5-second timeout.  

- **ws.request(url, command, argsJson, token):** A synchronous WebSocket client. If an authorization token is provided, the client automatically handles an initial authentication handshake (sending {"command": "auth"}) before sending the primary command. It waits up to 15 seconds for a response before timing out.  

#### Logging (console)

Maps directly to Android's native Log system.

- **console.info(message), console.warn(message), console.error(message):** Logs messages tagged with Plugin:{PluginName}.  

### 3\. Standardized UI Components

To maintain visual consistency, plugins must construct their interfaces by inflating predefined XML layouts provided by the AriaCast framework.

| **Layout Name** | **Description & Visuals** | **Example Use Case** |
| --- | --- | --- |
| **item_plugin_header** | A simple text header used to categorize sections, styled with the primary color and uppercase text. | "Music Assistant Players" |
| **item_plugin_text** | A basic Material card containing informational body text. | "No players found on server." |
| **item_plugin_button** | A clickable Material card featuring a primary icon on the left, an action text label, and an arrow icon on the right. | **Action:** Login Dialog<br><br>**Text:** "Login to 192.168.1.1" |
| **item_plugin_media** | A complex card designed for media playback control. It features an album art thumbnail, track title, subtitle (e.g., Artist/Album), and a Play/Pause Material button. | **Title:** "AriaCast Multi-UI"<br><br>**Subtitle:** "Isolated • Native • Stylized" |
| **item_plugin_stat** | A data display card containing a secondary-colored label on the left and a bold, primary-colored value on the right. | **Label:** "Server Connection"<br><br>**Value:** "Authenticated • 192.168.1.1" |
| **item_plugin_switch** | A toggle control card. It displays a title and detailed description on the left, alongside a MaterialSwitch widget on the right. | **Title:** "Isolation Mode"<br><br>**Description:** "Ensures plugins don't overwrite each other's views." |
| **item_plugin_slider** | A card containing a text label and a continuous Slider widget ranging from 0.0 to 100.0. | **Label:** "Adjustment Label" (e.g., Volume control) |
| **item_plugin_progress** | A card displaying an indeterminate LinearProgressIndicator accompanied by a text label to indicate background tasks. | "Concurrent task running..." |
| **item_plugin_input** | A card featuring a TextInputEditText wrapped in an OutlinedBox layout for text entry. | Username/Password entry for server authentication |
| **item_plugin_image** | A card built to display an image with a centerCrop scale type and a minimum height of 100dp. | Displaying album art or external imagery |
| **item_plugin_divider** | A thin, semi-transparent horizontal line used to visually separate elements within the plugin UI. | Separating the Media UI from general info text |
| **item_server** | A card designed to display remote servers, featuring a slideshow icon, a bold server name, and a secondary text field for the host address. | **Name:** "Living Room Speaker"<br><br>**Host:** "MA Player • Playing" |
| **item_packet_log** | A specialized card for debugging, displaying packet direction (e.g., IN/OUT), type, timestamp, and a monospaced text area for a JSON payload. | **Direction:** OUT<br><br>**Type:** AUDIO<br><br>**Time:** 12:00:01.123 |


### 4\. Developer Guidelines & Best Practices

- **Strict Thread Isolation:** You must respect the boundaries of the UI and background threads. Any code that updates view text, adds views, or clears the screen MUST be wrapped inside ui.run(). Conversely, long-running loops (like polling servers) or synchronous HTTP requests MUST be wrapped in bg.run() to prevent Application Not Responding (ANR) crashes.  

- **UI Isolation Mode:** AriaCast handles plugin interfaces securely. Each plugin is assigned a dynamically generated, tag-identified LinearLayout container (e.g., plugin_sub_container_music_assistant). This ensures plugins cannot overwrite each other's views. However, because views append sequentially, you should generally call ui.clear() before rendering a new UI state.  

- **State Management:** Always implement events.onStateChanged. If the casting service state drops (e.g., disconnects), handle the teardown by clearing your interface and resetting local state variables to prevent phantom UI updates.  

- **Handling Permissions:** When users specify a custom plugin folder, AriaCast uses Android's Storage Access Framework (OpenDocumentTree) to request and persist URI read/write permissions. Store custom files exclusively in this assigned directory; avoid hardcoded file paths as Scoped Storage restrictions apply.
