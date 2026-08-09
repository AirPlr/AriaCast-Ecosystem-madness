package com.example.ariacastreceiver

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.TransitionDrawable
import android.os.Bundle
import android.view.GestureDetector
import android.view.MotionEvent
import android.widget.EditText
import android.widget.ImageView
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.constraintlayout.widget.ConstraintLayout
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.lifecycleScope
import androidx.palette.graphics.Palette
import coil.load
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import java.util.Locale

class MainActivity : AppCompatActivity() {
    private lateinit var server: AriaCastServer
    private var currentArtUrl: String? = null

    private lateinit var title: TextView
    private lateinit var artist: TextView
    private lateinit var currentTime: TextView
    private lateinit var totalTime: TextView
    private lateinit var progressBar: ProgressBar
    private lateinit var rootLayout: ConstraintLayout

    private lateinit var gestureDetector: GestureDetector

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        hideSystemUI()

        val prefs = getSharedPreferences("AriaCastPrefs", Context.MODE_PRIVATE)
        val savedName = prefs.getString("receiver_name", "AriaCast Android") ?: "AriaCast Android"

        server = AriaCastServer(applicationContext, ServerConfig(serverName = savedName))
        server.start()

        rootLayout = findViewById(R.id.root_layout)
        val artwork = findViewById<ImageView>(R.id.artwork)
        title = findViewById(R.id.title)
        artist = findViewById(R.id.artist)
        progressBar = findViewById(R.id.progress_bar)
        currentTime = findViewById(R.id.current_time)
        totalTime = findViewById(R.id.total_time)

        artwork.setImageResource(R.drawable.ic_ariacast)

        setupGestures()

        // 1. Track Info (Title, Artist, Duration)
        lifecycleScope.launch {
            server.metadata
                .distinctUntilChanged { old, new ->
                    old.title == new.title && old.artist == new.artist && old.durationMs == new.durationMs
                }
                .collect { meta ->
                    title.text = meta.title ?: "AriaCast Receiver"
                    artist.text = meta.artist ?: "Ready to cast"
                    totalTime.text = formatTime(meta.durationMs)
                    progressBar.max = (meta.durationMs / 1000).toInt().coerceAtLeast(1)
                }
        }

        // 2. Artwork Handling (Using Bytes for maximum compatibility)
        lifecycleScope.launch {
            server.artworkBytes.collect { bytes ->
                if (bytes != null) {
                    artwork.load(bytes) {
                        crossfade(true)
                        allowHardware(false)
                        listener(onSuccess = { _, result ->
                            val bitmap = (result.drawable as? BitmapDrawable)?.bitmap
                            if (bitmap != null) {
                                updateBackgroundFromArtwork(bitmap)
                            }
                        })
                    }
                } else {
                    artwork.setImageResource(R.drawable.ic_ariacast)
                }
            }
        }

        // 3. Position Updates (Frequent but cheap)
        lifecycleScope.launch {
            server.metadata
                .map { it.positionMs }
                .distinctUntilChanged()
                .collect { pos ->
                    progressBar.progress = (pos / 1000).toInt()
                    currentTime.text = formatTime(pos)
                }
        }
    }

    private fun setupGestures() {
        gestureDetector = GestureDetector(this, object : GestureDetector.SimpleOnGestureListener() {
            override fun onDown(e: MotionEvent): Boolean = true
            
            private var lastTapTime = 0L
            private var tapCount = 0

            override fun onSingleTapUp(e: MotionEvent): Boolean {
                val now = System.currentTimeMillis()
                if (now - lastTapTime < 350) {
                    tapCount++
                } else {
                    tapCount = 1
                }
                lastTapTime = now

                if (tapCount == 3) {
                    showSettingsDialog()
                    tapCount = 0
                }
                return true
            }
        })

        rootLayout.setOnTouchListener { _, event ->
            gestureDetector.onTouchEvent(event)
        }
    }

    private fun showSettingsDialog() {
        val prefs = getSharedPreferences("AriaCastPrefs", Context.MODE_PRIVATE)
        val currentName = prefs.getString("receiver_name", "AriaCast Android") ?: "AriaCast Android"

        val input = EditText(this)
        input.setText(currentName)
        input.setPadding(64, 32, 64, 32)

        AlertDialog.Builder(this)
            .setTitle("Receiver Settings")
            .setMessage("Choose a name for this receiver:")
            .setView(input)
            .setPositiveButton("Save") { _, _ ->
                val newName = input.text.toString()
                if (newName.isNotBlank()) {
                    prefs.edit().putString("receiver_name", newName).apply()
                    server.updateName(newName)
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun hideSystemUI() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).let { controller ->
            controller.hide(WindowInsetsCompat.Type.systemBars())
            controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    private fun updateBackgroundFromArtwork(bitmap: Bitmap) {
        Palette.from(bitmap).generate { palette ->
            val defaultStartColor = ContextCompat.getColor(this, R.color.default_gradient_start)
            val defaultEndColor = ContextCompat.getColor(this, R.color.default_gradient_end)

            val swatches = palette?.swatches?.sortedByDescending { it.population } ?: listOf()
            val startColor = swatches.getOrNull(0)?.rgb ?: defaultStartColor
            val endColor = swatches.getOrNull(1)?.rgb ?: palette?.getDarkVibrantColor(defaultEndColor) ?: defaultEndColor

            val newGradient = GradientDrawable(
                GradientDrawable.Orientation.TL_BR,
                intArrayOf(startColor, endColor)
            )

            val oldDrawable = rootLayout.background
            val transition = TransitionDrawable(arrayOf(oldDrawable, newGradient))
            rootLayout.background = transition
            transition.isCrossFadeEnabled = true
            transition.startTransition(800)

            updateUIColors(startColor)
        }
    }

    private fun updateUIColors(backgroundColor: Int) {
        val luminance = 0.2126 * Color.red(backgroundColor) / 255 +
                        0.7152 * Color.green(backgroundColor) / 255 +
                        0.0722 * Color.blue(backgroundColor) / 255
        
        val primaryColor = if (luminance > 0.5) Color.BLACK else Color.WHITE
        val secondaryColor = if (luminance > 0.5) Color.argb(180, 0, 0, 0) else Color.argb(180, 255, 255, 255)

        title.setTextColor(primaryColor)
        artist.setTextColor(secondaryColor)
        currentTime.setTextColor(secondaryColor)
        totalTime.setTextColor(secondaryColor)
        
        progressBar.progressTintList = ColorStateList.valueOf(primaryColor)
        progressBar.progressBackgroundTintList = ColorStateList.valueOf(secondaryColor)
    }

    private fun formatTime(millis: Long): String {
        val totalSeconds = millis / 1000
        val minutes = totalSeconds / 60
        val seconds = totalSeconds % 60
        return String.format(Locale.getDefault(), "%d:%02d", minutes, seconds)
    }

    override fun onDestroy() {
        super.onDestroy()
        server.stop()
    }
}
