package com.assistant.mobile

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.speech.RecognizerIntent
import android.speech.tts.TextToSpeech
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.util.Locale


class MainActivity : ComponentActivity() {
    private var tts: TextToSpeech? = null

    private val speechLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            val data = result.data
            val spoken = data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()
            if (!spoken.isNullOrBlank()) {
                pendingSpeechResult?.invoke(spoken)
            }
        }
    }

    private var pendingSpeechResult: ((String) -> Unit)? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        tts = TextToSpeech(this) { status ->
            if (status == TextToSpeech.SUCCESS) {
                tts?.language = Locale.US
            }
        }

        setContent {
            val messages = remember { mutableStateListOf<ChatMessage>() }
            val inputState = remember { mutableStateOf("") }
            val status = remember { mutableStateOf("Ready") }

            LaunchedEffect(Unit) {
                messages.add(ChatMessage("Assistant ready on Android.", false))
            }

            Surface(
                modifier = Modifier.fillMaxSize(),
                color = Color(0xFF0F1116)
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(20.dp)
                ) {
                    Text(
                        text = "What can I help with?",
                        fontSize = 28.sp,
                        color = Color(0xFFF3F5F7),
                        fontWeight = FontWeight.SemiBold
                    )
                    Text(
                        text = "Status: ${status.value}",
                        color = Color(0xFF9BA5B4),
                        fontSize = 12.sp
                    )
                    Spacer(modifier = Modifier.height(12.dp))

                    LazyColumn(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth()
                    ) {
                        items(messages) { message ->
                            MessageBubble(message)
                        }
                    }

                    Spacer(modifier = Modifier.height(12.dp))

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        TextField(
                            value = inputState.value,
                            onValueChange = { inputState.value = it },
                            modifier = Modifier.weight(1f),
                            placeholder = { Text("Ask anything") }
                        )
                        Button(onClick = {
                            startVoiceInput { result ->
                                inputState.value = result
                            }
                        }) {
                            Text("Mic")
                        }
                        Button(onClick = {
                            val text = inputState.value.trim()
                            if (text.isNotEmpty()) {
                                inputState.value = ""
                                messages.add(ChatMessage(text, true))
                                val response = CommandRouter.handleCommand(this@MainActivity, text)
                                messages.add(ChatMessage(response.response, false))
                                if (response.speak) {
                                    tts?.speak(response.response, TextToSpeech.QUEUE_FLUSH, null, "assistant")
                                }
                            }
                        }) {
                            Text("Send")
                        }
                    }
                }
            }
        }
    }

    private fun startVoiceInput(onResult: (String) -> Unit) {
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault())
        }
        pendingSpeechResult = onResult
        speechLauncher.launch(intent)
    }

    override fun onDestroy() {
        tts?.stop()
        tts?.shutdown()
        super.onDestroy()
    }
}

@Composable
private fun MessageBubble(message: ChatMessage) {
    val background = if (message.isUser) Color(0xFF1D2835) else Color(0xFF13151C)
    val alignment = if (message.isUser) Alignment.End else Alignment.Start
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (message.isUser) Arrangement.End else Arrangement.Start
    ) {
        Column(
            modifier = Modifier
                .background(background, RoundedCornerShape(16.dp))
                .padding(12.dp)
                .align(alignment)
        ) {
            Text(text = message.text, color = Color(0xFFF3F5F7))
        }
    }
    Spacer(modifier = Modifier.height(8.dp))
}

data class ChatMessage(val text: String, val isUser: Boolean)

data class AssistantResponse(val response: String, val speak: Boolean = true)
