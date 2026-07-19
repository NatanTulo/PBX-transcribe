# PBX Transcribe — MVP

Lokalny, domyślnie całkowicie offline'owy pipeline do transkrypcji rozmów PBX,
diaryzacji, konserwatywnej korekty LLM oraz przeglądania zmian `przed → po`.

## Zasady prywatności

- nazwy plików wejściowych nie trafiają do wynikowego JSON ani standardowych logów;
- nagrania otrzymują stabilne, nieodwracalne identyfikatory `rec_...`;
- lokalny wizualizator pokazuje nazwę pliku audio, odtwarzając ją z prywatnego
  katalogu nagrań; nazwa nadal nie jest zapisywana w JSON;
- domyślnie dozwolony jest wyłącznie LLM pod adresem loopback (`localhost`);
- błędy workera zapisują tylko typ wyjątku, bez komunikatu mogącego zawierać ścieżkę lub tekst;
- polecenie `audit` odczytuje wyłącznie metadane techniczne przez `ffprobe`;
- katalogi z nagraniami, modelami, bazą kolejki i wynikami są ignorowane przez Git.

Wynikowa transkrypcja sama w sobie zawiera dane poufne i musi pozostać na chronionym
dysku. Interfejs nasłuchuje domyślnie tylko na `127.0.0.1`.

## Format wyniku

Każde nagranie tworzy `output/rec_<hash>.json`. `raw_text` jest niezmiennym wynikiem
STT, a `corrected_text` jego poprawioną wersją. Tablica `corrections` przechowuje
pozycje znakowe, tekst przed/po, kategorię i pewność. Schemat ma pole
`schema_version`, dzięki czemu późniejsza migracja do bazy nie wymaga zmiany
formatu wejściowego wizualizatora.

## Szybki start na Windows 11

Wymagane są Python 3.11+, FFmpeg/ffprobe, aktualny sterownik NVIDIA oraz CUDA
obsługiwana przez CTranslate2. PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[stt,dev]"
Copy-Item config.example.json config.json
pbx-transcribe audit
pbx-transcribe enqueue
pbx-transcribe worker --limit 1
pbx-transcribe serve
```

Wizualizator będzie dostępny na `http://127.0.0.1:8765`.

## Przenośny wizualizator Windows

Samodzielny EXE nie wymaga Pythona ani instalowania pakietów na komputerze
docelowym. Oczekiwany układ katalogu jest następujący:

```text
PBX-Transcribe-Viewer\
├── PBX-Transcribe-Viewer.exe
├── output_full\
└── audio\
```

Dwukrotne kliknięcie EXE uruchamia serwer wyłącznie na `127.0.0.1` i otwiera
domyślną przeglądarkę. Jeżeli port 8765 jest zajęty, aplikacja automatycznie
wybierze wolny port. Wizualizator pokazuje nazwy plików audio, a odtwarzacz
obsługuje strumieniowanie i przewijanie nagrań.

Budowanie samego EXE:

```powershell
.\build_portable_viewer.ps1
```

Budowanie EXE wraz z kopią bieżących `output_full` i `rozmowy`:

```powershell
.\build_portable_viewer.ps1 -BundleData
```

Druga komenda tworzy dodatkową kopię poufnych danych w `dist`, dlatego katalog
wynikowy należy chronić tak samo jak oryginalne nagrania i transkrypcje.

Modele nie są pobierane przez repozytorium. Przykładowa konfiguracja wskazuje na
`models/faster-whisper-large-v3`. Model można przygotować jednorazowo poleceniem
`hf download Systran/faster-whisper-large-v3 --local-dir models/faster-whisper-large-v3`.
Dla środowiska air-gapped należy skopiować zweryfikowane artefakty na serwer;
inferencja nie wymaga później sieci.

## llama.cpp i Bielik

Uruchom lokalny `llama-server` z oficjalnym modelem Bielik 11B v3 GGUF, przykładowo:

```powershell
tools\llama.cpp\llama-server.exe -m models\bielik-11b-v3.0-instruct\Bielik-11B-v3.0-Instruct.Q5_K_M.gguf --host 127.0.0.1 --port 8080 -ngl 99 -c 8192
```

Następnie ustaw `correction.enabled` na `true`. Dla `llama.cpp` aplikacja korzysta
z lokalnego endpointu `/completion` i natywnego `json_schema`. Omija to problemy
gramatyki szablonu czatu Bielika i gwarantuje strukturalnie poprawny JSON.
Integracja z Ollama lub vLLM wymaga adaptera transportu, ale nie zmienia schematu
zapisywanej transkrypcji.

Korektor:

- nie nadpisuje `raw_text`;
- wylicza dokładne różnice znakowe;
- odrzuca odpowiedź zmieniającą liczby;
- odrzuca endpoint sieciowy, dopóki `allow_remote` nie zostanie świadomie ustawione
  na `true` (dla tego projektu nie powinno być ustawiane).
- zapisuje checkpoint STT i diaryzacji przed uruchomieniem LLM;
- ponawia błędne odpowiedzi z wykładniczym backoffem;
- dzieli trwale wadliwą partię aż do pojedynczych segmentów;
- w razie całkowitej awarii korekty zachowuje surową transkrypcję zamiast oznaczać
  całe nagranie jako nieudane.

## Diaryzacja

Dostarczone WAV są mono, dlatego rozdzielenie kanałów nie jest dostępne. Aby
włączyć pyannote:

1. osobno zweryfikuj i zaakceptuj warunki konkretnego modelu;
2. pobierz kompletny model do lokalnego katalogu;
3. zainstaluj `python -m pip install -e ".[diarization]"`;
4. ustaw lokalny `diarization.model_path` oraz `diarization.enabled: true`.

Pipeline preferuje `exclusive_speaker_diarization`, jeżeli zwraca ją zainstalowana
wersja pyannote, a potem przypisuje mówcę na poziomie segmentów i słów.

## Kolejka i polecenia

```text
pbx-transcribe audit                 zagregowane parametry techniczne WAV
pbx-transcribe enqueue               dodanie nowych WAV do kolejki SQLite
pbx-transcribe retry-failed          ponowienie zadań zakończonych błędem
pbx-transcribe retry-interrupted     odzyskanie zadania po przerwanym workerze
pbx-transcribe worker --limit N      przetworzenie N zadań; 0 = do opróżnienia
pbx-transcribe process REC_ID        pojedyncze nagranie po anonimowym ID
pbx-transcribe serve                 lokalny wizualizator
pbx-transcribe metrics REF HYP       tylko zagregowane WER/CER, bez wypisywania tekstu
```

Do bezpiecznego testu instalacji bez rozpoznawania mowy służy:

```powershell
pbx-transcribe process REC_ID --fixture
```

## Co mierzyć przed strojeniem

- WER i CER surowego Faster-Whisper;
- WER i CER po korekcie LLM;
- odsetek zmian zaakceptowanych i odrzuconych przez człowieka;
- DER/JER diaryzacji na ręcznie oznaczonej próbce;
- real-time factor każdego etapu oraz czas oczekiwania w kolejce;
- zużycie VRAM osobno dla STT, diaryzacji i LLM.

WER/CER wymagają referencyjnej transkrypcji. Sam zestaw WAV pozwala zmierzyć
parametry audio i wydajność, ale nie obiektywną dokładność tekstu.
