# Guida Pipeline Video Automatizzata

Questa guida descrive il workflow per generare video musicali con sottotitoli in stile karaoke, organizzati per progetto.

## Struttura Directory

Tutti gli asset di input e output sono ora organizzati per nome progetto:

```text
input_automated/
└── <nome_progetto>/
    ├── audio/          ← Brano musicale completo (mp3/wav)
    ├── vocal_stems/    ← Stem solo voce (usato per trascrizione)
    ├── lyrics/         ← Testo in formato .txt
    ├── subtitles/      ← (Generato) File .srt, .ass, .json
    ├── video_clips/    ← Clip video sorgente (.mp4/.mov)
    └── watermark/      ← Filmato watermark (opzionale)

output_automated/
└── <nome_progetto>/    ← Video montati e sottotitolati
```

## Workflow Passaggio-Passaggio

In tutti gli script è possibile usare il parametro `--project <nome>` per agire automaticamente sulle cartelle del progetto specifico.

### 1. Preparazione Asset ([1_organize_and_delogo.py](file:///Volumes/Ext_drive/dev/ai_video_maker/1_organize_and_delogo.py))
Organizza e rimuove i loghi (delogo) dalle clip video grezze in `input_delogo/`.
- **Esempio**: `python3 1_organize_and_delogo.py --project relive`
- **Opzioni principali**:
    - `--project`: Nome del progetto.
    - `--show-logo`: Mostra l'area del logo invece di rimuoverla (per debugging).

## Interfaccia Grafica (GUI)
Per un'esperienza più intuitiva, puoi lanciare la GUI:
```bash
python3 gui.py
```
L'interfaccia riproduce esattamente i 4 step descritti sotto. In ogni tab troverai un campo **Project Name** che devi compilare per far sì che la GUI punti alle cartelle corrette del tuo progetto.

---

### 2. Sincronizzazione Testo e Audio ([2_auto_lyrics_subtitles_groq.py](file:///Volumes/Ext_drive/dev/ai_video_maker/2_auto_lyrics_subtitles_groq.py))
Utilizza Groq Whisper per trascrivere la stem vocale e allinearla al testo fornito.
- **Esempio**: `python3 2_auto_lyrics_subtitles_groq.py --project relive`
- **Opzioni principali**:
    - `--subtitle-mood`: Preset di stile (es: `neon_night`, `warm_cinematic`).
    - `--no-chunking`: Disabilita lo split dell'audio (usare solo per brani brevi).

### 3. Montaggio Video Automizzato ([3_automated_music_video.py](file:///Volumes/Ext_drive/dev/ai_video_maker/3_automated_music_video.py))
Assembled le clip video rimosse dai loghi in un montaggio casuale sincronizzato con la durata dell'audio.
- **Esempio**: `python3 3_automated_music_video.py --project relive`
- **Opzioni principali**:
    - `--width` / `--height`: Risoluzione output (default 1080x1920).
    - `--transition-duration`: Durata transizioni tra clip (default 1.0s).

### 4. Burning Sottotitoli ([4_add_subtitles_to_video.py](file:///Volumes/Ext_drive/dev/ai_video_maker/4_add_subtitles_to_video.py))
Applica i sottotitoli (standard o karaoke) al video generato.
- **Esempio**: `python3 4_add_subtitles_to_video.py --project relive --karaoke`
- **Opzioni principali**:
    - `--karaoke`: Abilita l'effetto highlight parola per parola.
    - `--style`: Cambia il preset visivo.
    - `--platform`: Posiziona i sottotitoli sopra la UI di `instagram_reels`, `tiktok`, `youtube_shorts`.
    - `--hwaccel`: Usa l'accelerazione hardware Apple Silicon (`h264_videotoolbox`) per un encode ultra-veloce.

---

## Suggerimenti Rapidi
- Assicurati che [groq.conf](file:///Volumes/Ext_drive/dev/ai_video_maker/groq.conf) contenga la tua `GROQ_API_KEY`.
- Se i sottotitoli non sono perfettamente a tempo, usa `--subtitle-offset <secondi>` in [4_add_subtitles_to_video.py](file:///Volumes/Ext_drive/dev/ai_video_maker/4_add_subtitles_to_video.py).
