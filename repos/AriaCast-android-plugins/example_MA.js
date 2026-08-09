console.info("MA Plugin 1.9.3 Loaded");

var currentHost = null;
var isPolling = false;
var ariacastSourceId = "ariacast";
var ariacastName = "AriaCast";

            function startPolling(host) {
                if (isPolling) return;
                isPolling = true;
                bg.run(function() {
                    console.info("MA: Starting polling for " + host);
                    while(currentHost === host) {
                        try {
                            renderMAUI(host);
                        } catch(e) {
                            console.error("MA: Refresh loop error: " + e);
                        }
                        java.lang.Thread.sleep(10000); 
                    }
                    isPolling = false;
                    console.info("MA: Polling stopped for " + host);
                });
            }

            events.onServiceConnected(function(s) {
                // Just update service reference, UI handled by onStateChanged
                console.info("MA: Service connected");
                if (s) {
                    storage.set("last_host", s.serverHost || "");
                }
            });

            events.onStateChanged(function(state) {
                if (!service) {
                    ui.clear();
                    currentHost = null;
                    return;
                }

                var name = String(service.serverName || "");
                var host = String(service.serverHost || "");
                var isMA = name.toLowerCase().indexOf("musicassistant") !== -1;

                console.info("MA: State changed to " + state + " for " + name);

                if (isMA && (state === "CONNECTING" || state === "CASTING")) {
                    if (host !== currentHost) {
                        currentHost = host;
                        startPolling(host);
                    }
                    renderMAUI(host);
                } else {
                    ui.clear();
                    currentHost = null;
                }
            });

            function getAuthHeaders() {
                var token = storage.get("auth_token");
                if (token) {
                    var t = String(token).replace("Bearer ", "").replace(/"/g, "").trim();
                    return JSON.stringify({ "Authorization": "Bearer " + t });
                }
                return null;
            }

            function renderMAUI(host) {
                if (host !== currentHost) return; // Prevent rendering if host changed
                
                var token = storage.get("auth_token");
                var baseUrl = "http://" + host + ":8095";
                var wsUrl = "ws://" + host + ":8095/ws";
                
                var playersJson = null;

                if (token) {
                    playersJson = ws.request(wsUrl, "players/all", null, token);
                }

                if (!playersJson || String(playersJson).indexOf("Error") === 0) {
                    var t = String(token || "").replace("Bearer ", "").replace(/"/g, "").trim();
                    var rpcBody = JSON.stringify({ command: "players/all", args: {} });
                    var headers = t ? JSON.stringify({ "Authorization": "Bearer " + t }) : null;
                    playersJson = http.post(baseUrl + "/api", rpcBody, headers);
                }

                ui.run(function() {
                    // Check again inside UI thread
                    if (host !== currentHost) return;

                    ui.clear();
                    
                    var title = new android.widget.TextView(activity);
                    title.setText("Music Assistant Players");
                    title.setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_LabelLarge);
                    title.setPadding(40, 30, 0, 10);
                    ui.add(title);

                    var isAuthenticated = playersJson && String(playersJson).indexOf("Error") !== 0;

                    if (!isAuthenticated) {
                        var btnLayout = new android.widget.LinearLayout(activity);
                        btnLayout.setOrientation(0);
                        btnLayout.setPadding(40, 0, 40, 0);
                        var lp = new android.widget.LinearLayout.LayoutParams(0, -2, 1);
                        
                        var loginBtn = new android.widget.Button(activity, null, com.google.android.material.R.attr.materialButtonOutlinedStyle);
                        loginBtn.setText("Login");
                        loginBtn.setOnClickListener(function() { showLoginDialog(host); });
                        loginBtn.setLayoutParams(lp);
                        btnLayout.addView(loginBtn);

                        var tokenBtn = new android.widget.Button(activity, null, com.google.android.material.R.attr.materialButtonOutlinedStyle);
                        tokenBtn.setText("Token");
                        tokenBtn.setOnClickListener(function() { showTokenDialog(host); });
                        tokenBtn.setLayoutParams(lp);
                        btnLayout.addView(tokenBtn);

                        ui.add(btnLayout);
                    } else {
                        var statusTxt = new android.widget.TextView(activity);
                        statusTxt.setText("Authenticated • " + host);
                        statusTxt.setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_BodySmall);
                        statusTxt.setPadding(40, 0, 0, 20);
                        statusTxt.setAlpha(0.6);
                        ui.add(statusTxt);
                    }

                    if (playersJson && String(playersJson).length > 0 && String(playersJson).indexOf("Error") !== 0) {
                        try {
                            var players = JSON.parse(playersJson);
                            var list = Array.isArray(players) ? players : (players.results || []);
                            
                            if (list.length === 0) {
                                var emptyTxt = new android.widget.TextView(activity);
                                emptyTxt.setText("No players found on server.");
                                emptyTxt.setPadding(40, 10, 0, 10);
                                ui.add(emptyTxt);
                                return;
                            }

                            var typedValue = new android.util.TypedValue();
                            activity.getTheme().resolveAttribute(com.google.android.material.R.attr.colorPrimary, typedValue, true);
                            var accentColor = typedValue.data;

                            list.forEach(function(p) {
                                var itemView = ui.inflate("item_server");
                                var nameTxt = ui.findView(itemView, "serverName");
                                var hostTxt = ui.findView(itemView, "serverHost");
                                
                                var name = p.name || p.display_name || "Unknown Player";
                                var id = p.player_id || p.id;
                                var state = p.state || (p.active ? "Active" : "Idle");

                                if (nameTxt) nameTxt.setText(name);
                                if (hostTxt) hostTxt.setText("MA Player • " + state);

                                var isPlaying = String(state).toLowerCase() === "playing";
                                if (isPlaying) {
                                    itemView.setStrokeWidth(6);
                                    itemView.setStrokeColor(android.content.res.ColorStateList.valueOf(accentColor));
                                } else {
                                    itemView.setStrokeWidth(0);
                                }

                                var isActive = p.active && (p.active_source && p.active_source.indexOf("ariacast") !== -1);
                                if (isActive) {
                                    itemView.scaleX = 1.02;
                                    itemView.scaleY = 1.02;
                                }

                                itemView.setOnClickListener(function(v) {
                                    bg.run(function() {
                                        var t = String(storage.get("auth_token") || "").replace("Bearer ", "").replace(/"/g, "").trim();
                                        var rpcUrl = baseUrl + "/api";
                                        var h = t ? JSON.stringify({ "Authorization": "Bearer " + t }) : null;
                                        
                                        var sourceId = null;
                                        var sourcesRes = ws.request(wsUrl, "players/plugin_sources", null, t);
                                        if (!sourcesRes || String(sourcesRes).indexOf("Error") === 0) {
                                            sourcesRes = http.post(rpcUrl, JSON.stringify({ command: "players/plugin_sources" }), h);
                                        }
                                        
                                        if (sourcesRes && String(sourcesRes).indexOf("Error") !== 0) {
                                            try {
                                                var sources = JSON.parse(sourcesRes);
                                                for (var i = 0; i < sources.length; i++) {
                                                    var src = sources[i];
                                                    if (src.source_id === ariacastSourceId || src.domain === ariacastSourceId || (src.name && src.name.indexOf(ariacastName) !== -1)) {
                                                        sourceId = src.source_id || src.id || src.domain;
                                                        break;
                                                    }
                                                }
                                            } catch(e) {}
                                        }
                                        
                                        if (!sourceId) {
                                            var providersRes = ws.request(wsUrl, "providers", null, t);
                                            if (!providersRes || String(providersRes).indexOf("Error") === 0) {
                                                providersRes = http.post(rpcUrl, JSON.stringify({ command: "providers" }), h);
                                            }
                                            if (providersRes && String(providersRes).indexOf("Error") !== 0) {
                                                try {
                                                    var providers = JSON.parse(providersRes);
                                                    for (var j = 0; j < providers.length; j++) {
                                                        var prov = providers[j];
                                                        if (prov.domain === ariacastSourceId || (prov.name && prov.name.indexOf(ariacastName) !== -1)) {
                                                            sourceId = prov.instance_id || prov.domain;
                                                            break;
                                                        }
                                                    }
                                                } catch(e) {}
                                            }
                                        }
                                        
                                        if (sourceId) {
                                            var selectBody = JSON.stringify({ 
                                                command: "players/cmd/select_source", 
                                                args: { player_id: id, source: sourceId } 
                                            });
                                            http.post(rpcUrl, selectBody, h);
                                        } else {
                                            var fallbackBody = JSON.stringify({ command: "players/cmd/select_source", args: { player_id: id, source: ariacastSourceId } });
                                            http.post(rpcUrl, fallbackBody, h);
                                        }
                                        
                                        java.lang.Thread.sleep(1000);
                                        renderMAUI(host);
                                    });
                                    android.widget.Toast.makeText(activity, "Targeting " + name, 0).show();
                                });

                                ui.add(itemView);
                            });
                        } catch(e) { 
                            console.error("MA: Parse error: " + e); 
                        }
                    } else {
                        var msg = new android.widget.TextView(activity);
                        var errMsg = "Check connection and token.";
                        if (!storage.get("auth_token")) errMsg = "Authentication required.";
                        else if (String(playersJson).indexOf("Error") === 0) errMsg = playersJson;
                        
                        msg.setText(errMsg);
                        msg.setPadding(40, 10, 0, 10);
                        ui.add(msg);
                    }
                });
            }

            function showLoginDialog(host) {
                var builder = new com.google.android.material.dialog.MaterialAlertDialogBuilder(activity);
                builder.setTitle("Music Assistant Login");
                var layout = new android.widget.LinearLayout(activity);
                layout.setOrientation(1);
                layout.setPadding(60, 20, 60, 0);
                var userField = new android.widget.EditText(activity);
                userField.setHint("Username");
                layout.addView(userField);
                var passField = new android.widget.EditText(activity);
                passField.setHint("Password");
                passField.setInputType(129);
                layout.addView(passField);
                builder.setView(layout);
                builder.setPositiveButton("Login", function(d, w) {
                    var username = userField.getText().toString();
                    var password = passField.getText().toString();
                    bg.run(function() {
                        var loginBody = JSON.stringify({ "username": username, "password": password });
                        var loginUrl = "http://" + host + ":8095/auth/login";
                        var loginResponse = http.post(loginUrl, loginBody);
                        if (loginResponse && (String(loginResponse).indexOf("access_token") !== -1 || String(loginResponse).indexOf("token") !== -1)) {
                            var data = JSON.parse(loginResponse);
                            var token = data.access_token || data.token;
                            if (token) {
                                storage.set("auth_token", token);
                                renderMAUI(host); 
                            }
                        }
                    });
                });
                builder.setNegativeButton("Cancel", null);
                builder.show();
            }

            function showTokenDialog(host) {
                var builder = new com.google.android.material.dialog.MaterialAlertDialogBuilder(activity);
                builder.setTitle("Music Assistant Token");
                var input = new android.widget.EditText(activity);
                input.setHint("Token");
                input.setText(storage.get("auth_token") || "");
                var padding = 60;
                var container = new android.widget.FrameLayout(activity);
                container.setPadding(padding, 20, padding, 0);
                container.addView(input);
                builder.setView(container);
                builder.setPositiveButton("Save", function() {
                    var token = input.getText().toString().trim();
                    if (token) {
                        storage.set("auth_token", token);
                        renderMAUI(host);
                    }
                });
                builder.setNeutralButton("Clear", function() {
                    storage.set("auth_token", "");
                    renderMAUI(host);
                });
                builder.show();
            }
            
            // Register external configuration call
            events.onConfigRequested(function() {
                var host = storage.get("last_host") || "127.0.0.1";
                var builder = new com.google.android.material.dialog.MaterialAlertDialogBuilder(activity);
                builder.setTitle("Music Assistant Setup");
                builder.setMessage("Choose how you want to authenticate with the server.");
                builder.setPositiveButton("Login", function() { showLoginDialog(host); });
                builder.setNeutralButton("Token", function() { showTokenDialog(host); });
                builder.show();
            });
