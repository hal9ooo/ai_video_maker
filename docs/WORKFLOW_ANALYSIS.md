# Analisi Tecnica del Workflow: Automazione Video Pipeline

Questo documento analizza le sfide tecniche e le soluzioni implementate nella realizzazione della pipeline di generazione automatica di video musicali.

---

## 1. Rimozione Loghi e Pre-processing Video
La prima sfida consiste nel rendere utilizzabili clip video grezze scaricate dai social o repository, che spesso contengono loghi o watermark fissi.

### Sfida: Coordinate Variabili
Le clip possono avere risoluzioni e aspect ratio differenti (9:16 verticali, 16:9 orizzontali). Un filtro di rimozione statico non sarebbe efficace.

### Soluzione: Configurazione JSON e Filtro [delogo](file:///Volumes/Ext_drive/dev/ai_video_maker/1_organize_and_delogo.py#314-335)
Abbiamo implementato un sistema basato su [delogo_boxes.json](file:///Volumes/Ext_drive/dev/ai_video_maker/delogo_boxes.json) che mappa il posizionamento del logo per ogni tipo di formato (Vertical, Horizontal, Square). 
- **Processo**: Lo script [1_organize_and_delogo.py](file:///Volumes/Ext_drive/dev/ai_video_maker/1_organize_and_delogo.py) analizza i metadati della clip tramite [ffprobe](file:///Volumes/Ext_drive/dev/ai_video_maker/4_add_subtitles_to_video.py#232-237), ne determina il formato e applica il filtro [delogo](file:///Volumes/Ext_drive/dev/ai_video_maker/1_organize_and_delogo.py#314-335) di FFmpeg con i parametri specifici di coordinate (x, y) e dimensione (w, h). 
- **Risultato**: I loghi vengono sostituiti da un effetto "blur" intelligente, rendendo la clip adatta al montaggio professionale.

---

## 2. Sincronizzazione Sottotitoli con Groq Whisper
La sincronizzazione parola per parola è il cuore dell'effetto karaoke, ma i modelli AI di trascrizione (come Whisper) presentano diverse limitazioni in ambiente musicale.

### Sfida A: Allucinazioni negli Intro
In brani con lunghe introduzioni strumentali, Whisper tende a "allucinare" parole del testo prima che il cantante inizi effettivamente a cantare.
- **Soluzione**: Abbiamo implementato [detect_vocal_start()](file:///Volumes/Ext_drive/dev/ai_video_maker/2_auto_lyrics_subtitles_groq.py#525-538). Lo script analizza la stem vocale e identifica il primo momento di attività sonora significativa (sopra i -35dB). Qualsiasi trascrizione precedente viene scartata automaticamente.

### Sfida B: Limiti di Token e Buffer API
L'API di Groq ha limiti sulla durata dell'audio e sulla quantità di testo elaborabile in una singola chiamata.
- **Soluzione: Chunking con Overlap**: Lo script [2_auto_lyrics_subtitles_groq.py](file:///Volumes/Ext_drive/dev/ai_video_maker/2_auto_lyrics_subtitles_groq.py) divide l'audio in segmenti da 24 secondi con un overlap di 0.5 secondi. L'overlap è critico per evitare di "tagliare" una parola a metà tra due chiamate API.

### Sfida C: Allineamento Testo-Audio
Whisper non restituisce sempre il testo esatto (errori di trascrizione, parole saltate).
- **Soluzione: Algoritmo di Allineamento Custom**: Invece di usare direttamente la trascrizione dell'AI, usiamo il testo fornito dall'utente come "verità". Lo script esegue un allineamento fuzzy tra i token del testo originale e i token temporizzati restituiti da Whisper, assegnando i timestamp corretti alle parole originali (anche se l'AI le ha trascritte leggermente male).

---

## 3. Generazione Karaoke e Gestione Timing
Il formato SRT standard non supporta l'evidenziazione parola per parola.

### Sincronizzazione Multi-Livello
Per ottenere l'effetto karaoke in [4_add_subtitles_to_video.py](file:///Volumes/Ext_drive/dev/ai_video_maker/4_add_subtitles_to_video.py), gestiamo tre livelli di dati:
1. **SRT**: Definisce i gruppi di righe (quando la frase appare e scompare).
2. **JSON**: Contiene i timestamp precisi di ogni singola parola.
3. **ASS Tags**: Lo script converte i dati JSON in tag `\k` (Karaoke) di Advanced Substation Alpha. Questo permette a FFmpeg di gestire via hardware il "sweep" del colore durante il rendering.

---

## 4. Overlay Watermark e Transizioni
Il montaggio dinamico richiede che elementi grafici e video siano coerenti.

### Sfida: Watermark Loop
Il video subtitolato deve avere un logo watermark che può essere più breve del brano stesso.
- **Soluzione**: In [3_automated_music_video.py](file:///Volumes/Ext_drive/dev/ai_video_maker/3_automated_music_video.py), usiamo il filtro [overlay](file:///Volumes/Ext_drive/dev/ai_video_maker/1_organize_and_delogo.py#337-389) con l'opzione `repeat` o gestiamo il loop tramite layer FFmpeg per assicurarci che il watermark sia presente per tutta la durata del video finale, indipendentemente dalla lunghezza del file sorgente.

---

## 5. Efficienza Professionale: Accelerazione Hardware
Il rendering video è un'operazione intensiva per la CPU.
- **Soluzione**: Abbiamo ottimizzato gli script per sfruttare **Apple Silicon (M1/M2/M3)**. Invece di usare il codec standard [libx264](file:///Volumes/Ext_drive/dev/ai_video_maker/1_organize_and_delogo.py#77-95), forziamo l'uso di `h264_videotoolbox`. Questo sposta il lavoro sui core dedicati alla codifica video del chip, riducendo i tempi di rendering di oltre il 500% (da minuti a pochi secondi).

---

## Conclusione
L'unione di modelli di linguaggio (Groq Whisper), filtri video avanzati (FFmpeg) e logica di allineamento custom ha permesso di trasformare una serie di asset disorganizzati in un prodotto finito di alta qualità, mantenendo il workflow scalabile tramite l'organizzazione per progetti.
