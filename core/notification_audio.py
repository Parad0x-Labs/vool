"""Original short local bubble chime, synthesized from bounded sine envelopes.

Contributor: sls_0x. This waveform is original project source, licensed under
this repository's license. No media services, codec downloads or runtime files.
"""
from __future__ import annotations
import base64
import io
import math
import struct
import wave


def sound_wav() -> bytes:
    rate, duration = 16000, 0.32
    samples = []
    for index in range(int(rate * duration)):
        t = index / rate
        envelope = min(1.0, t / 0.009) * math.exp(-t * 16) * max(0.0, 1 - t / duration)
        phase = 2 * math.pi * (920 * t - 460 * t * t)
        value = 0.12 * envelope * (math.sin(phase) + 0.24 * math.sin(phase * 1.5)) / 1.24
        samples.append(struct.pack('<h', round(32767 * value)))
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
