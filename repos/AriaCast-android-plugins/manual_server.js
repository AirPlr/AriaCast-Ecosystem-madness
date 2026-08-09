console.info("Manual Server Plugin Loaded");

function renderUI() {
    ui.run(function() {
        ui.clear();
        var header = ui.inflate("item_plugin_header");
        var ht = ui.findView(header, "headerText");
        if (ht) ht.setText("Manual Server Entry");
        ui.add(header);

        var ipInput = ui.inflate("item_plugin_input");
        var ipEdit = ui.findView(ipInput, "editText");
        if (ipEdit) {
            ipEdit.setHint("IP Address (e.g. 192.168.1.50)");
            ipEdit.setText(storage.get("last_manual_ip") || "");
        }
        ui.add(ipInput);

        var portInput = ui.inflate("item_plugin_input");
        var portEdit = ui.findView(portInput, "editText");
        if (portEdit) {
            portEdit.setHint("Port (Default: 12889)");
            portEdit.setInputType(2);
            portEdit.setText(storage.get("last_manual_port") || "12889");
        }
        ui.add(portInput);

        var btn = ui.inflate("item_plugin_button");
        var bt = ui.findView(btn, "buttonText");
        if (bt) bt.setText("Add Server");
        btn.setOnClickListener(function() {
            var ip = ipEdit.getText().toString().trim();
            var port = parseInt(portEdit.getText().toString().trim()) || 12889;
            if (ip && discovery) {
                if (discovery.addManualServer(ip, port, "Manual: " + ip)) {
                    storage.set("last_manual_ip", ip);
                    storage.set("last_manual_port", String(port));
                    android.widget.Toast.makeText(activity, "Server added", 0).show();
                }
            }
        });
        ui.add(btn);
    });
}
renderUI();
