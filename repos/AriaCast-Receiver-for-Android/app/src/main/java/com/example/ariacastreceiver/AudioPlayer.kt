package com.example.ariacastreceiver

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Log
import java.util.concurrent.LinkedBlockingQueue

class AudioPlayer(private val config: AudioConfig) {
    private var audioTrack: AudioTrack? = null
    private val frameQueue = LinkedBlockingQueue<ByteArray>(250) // Increased buffer for stability
    private var isPlaying = false
    private var playbackThread: Thread? = null

    fun start() {
        if (isPlaying) return
        
        val minBufferSize = AudioTrack.getMinBufferSize(
            config.sampleRate,
            AudioFormat.CHANNEL_OUT_STEREO,
            AudioFormat.ENCODING_PCM_16BIT
        )

        try {
            audioTrack = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build()
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(config.sampleRate)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO)
                        .build()
                )
                .setBufferSizeInBytes(minBufferSize.coerceAtLeast(config.frameSize * 20))
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()

            isPlaying = true
            audioTrack?.play()

            playbackThread = Thread {
                while (isPlaying) {
                    try {
                        val frame = frameQueue.poll(500, java.util.concurrent.TimeUnit.MILLISECONDS)
                        if (frame != null) {
                            audioTrack?.write(frame, 0, frame.size)
                        }
                    } catch (e: InterruptedException) {
                        break
                    } catch (e: Exception) {
                        Log.e("AudioPlayer", "Playback error: ${e.message}")
                    }
                }
            }.apply { 
                name = "AriaPlaybackThread"
                priority = Thread.MAX_PRIORITY 
                start() 
            }
            
            Log.i("AudioPlayer", "AudioPlayer started")
        } catch (e: Exception) {
            Log.e("AudioPlayer", "Failed to start AudioTrack: ${e.message}")
        }
    }

    fun stop() {
        isPlaying = false
        playbackThread?.interrupt()
        playbackThread = null
        try {
            audioTrack?.stop()
            audioTrack?.release()
        } catch (e: Exception) {}
        audioTrack = null
        frameQueue.clear()
        Log.i("AudioPlayer", "AudioPlayer stopped")
    }

    fun enqueueFrame(data: ByteArray) {
        if (!isPlaying) start()
        
        // If queue is full, drop the oldest frame to maintain low latency
        if (frameQueue.size >= 240) {
            frameQueue.poll()
        }
        frameQueue.offer(data)
    }
    
    fun clearQueue() {
        frameQueue.clear()
    }
}
