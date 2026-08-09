var vizView = null;

events.onStateChanged(function(state) {
    if (state === "CASTING") {
        ui.run(function() {
            ui.clear();
            var header = ui.inflate("item_plugin_header");
            ui.findView(header, "headerText").setText("Live Audio Waveform");
            ui.add(header);
            vizView = new com.aria.ariacast.VisualizerView(activity);
            vizView.setLayoutParams(new android.widget.LinearLayout.LayoutParams(-1, 300));
            var typedValue = new android.util.TypedValue();
            activity.getTheme().resolveAttribute(com.google.android.material.R.attr.colorPrimary, typedValue, true);
            vizView.setAccentColor(typedValue.data);
            ui.add(vizView);
        });
    } else { ui.clear(); vizView = null; }
});

events.onAudioBuffer(function(bytes) {
    if (vizView) vizView.updateVisualizer(bytes);
});
