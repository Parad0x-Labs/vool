"""Original soft water drop, synthesized from a bounded damped liquid bubble.

Contributor: sls_0x. This waveform is original project source, licensed under
this repository's license. No media services, codec downloads or runtime files.
"""
from __future__ import annotations

import base64
import io
import math
import random
import struct
import wave


def sound_wav() -> bytes:
    rate, duration = 16000, 0.20
    samples = []
    noise = random.Random(0)
    previous_noise = 0.0
    for index in range(int(rate * duration)):
        t = index / rate
        # A contracting bubble rises briefly in pitch, then dies away.
        phase = 2 * math.pi * (760 * t - 410 * 0.018 * (1 - math.exp(-t / 0.018)))
        envelope = (1 - math.exp(-t / 0.0018)) * math.exp(-t / 0.027)
        bubble = 0.095 * envelope * math.sin(phase)
        # A quiet, sub-10ms impact supplies texture without a ringing tail.
        white = noise.uniform(-1, 1)
        previous_noise = 0.55 * previous_noise + 0.45 * white
        impact = 0.012 * previous_noise * (1 - math.exp(-t / 0.0005)) * math.exp(-t / 0.003)
        fade = min(1.0, (duration - t) / 0.015)
        sample = round(32767 * (bubble + impact) * fade)
        # Keep the quantization of the approved, 30% quieter audition.
        samples.append(struct.pack('<h', round(sample * 0.70)))
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b''.join(samples))
    return output.getvalue()


def audio_script() -> str:
    asset = 'data:audio/wav;base64,' + base64.b64encode(sound_wav()).decode('ascii')
    return """<script>(function(){
if (window.VoolNotificationAudio) return;
var player = null, unlocked = false, last = 0, operation = 0;
function audio(){ if (!player) { player = new Audio(ASSET); player.volume = 0.65; } return player; }
function play(preview){
  if (!preview && (!unlocked || Date.now() - last < 750)) return Promise.resolve(false);
  try {
    operation++;
    var a = audio(); a.pause(); a.currentTime = 0; a.muted = false;
    last = Date.now();
    return Promise.resolve(a.play()).then(function(){ unlocked = true; return true; }).catch(function(){ return false; });
  } catch(e) { return Promise.resolve(false); }
}
function unlock(event){
  if (!event.isTrusted || unlocked) return;
  try {
    var token = ++operation, a = audio(); a.muted = true;
    Promise.resolve(a.play()).then(function(){
      unlocked = true;
      if (token === operation) { a.pause(); a.currentTime = 0; a.muted = false; }
    }).catch(function(){ if (token === operation) a.muted = false; });
  } catch(e) {}
}
document.addEventListener('pointerdown', unlock, true);
document.addEventListener('keydown', unlock, true);
window.VoolNotificationAudio = Object.freeze({ready:function(){return unlocked;}, preview:function(){return play(true);}, cue:function(){return play(false);}, mute:function(){if(player)player.pause();}});
})();</script>""".replace('ASSET', repr(asset))
