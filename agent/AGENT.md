# Ajan iş akışı (düşük token)

Kural: model blok/koordinat üretmez. Model yalnızca **parametre JSON'u** yazar; gerisi yerel.

1. İstek → en yakın preset'i seç (`python -m terrain_engine presets`). Dosyaları okuma; preset adları ve
   `params.py` içindeki `NUMERIC` tablosu yeterli.
2. Farkları `--set` ile ver. Bölgesel istekte ("batı orman, doğu kayalık") `regions` yaz (aşağıda).
3. `generate ... --verify` çalıştır. Sadece son satırları oku (`| Select-Object -Last 3`).
4. `CACHE HIT` görürsen tekrar üretme, tekrar doğrulama yapma, yeni model çağrısı başlatma.
5. Görsel kontrol gerekiyorsa sadece `previews/oblique.png` oku (tek görsel).
6. Revizyon = yalnızca değişen parametreler; motor kodu değişirse `ENGINE_VERSION` artır (cache geçersizleşir).

### Doğal dil → `regions` (az token)
- Yön → `{"shape":"half","side":"west|east|north|south"}`; "kuzeydoğu köşe" → `box` (x doğuya, z güneye artar, 0..width/length);
  "ortada göl/tepe" → `circle`; serbest alan → `polygon`. Sınır "yumuşak/keskin" → `blend` 64 / 8 (vars. 32).
- Bölgenin karakteri → önce en yakın `preset` ("orman" forest_hills, "kayalık/uçurum" rocky_cliffs, "dağ" mountain_valley,
  "düzlük" natural_plains, "fantastik orman" fantasy_forest_valley); ince ayar `set` ile (yalnız harmanlanabilir anahtarlar).
- Global parametreler (seed, boyut, su, erosion, hydraulic, nehir, noise_scale, edge_falloff) üst seviyede kalır; bölgede hata verir.
- `--params istek.json` ile ver; tek dosya: `{"regions":[...], ...üst seviye farklar}`. Hata mesajı izinli anahtarları listeler.

### Var olan dünya
"Haritamın şu bölgesini geliştir/değiştir" → `edit-world --source <dünya> --out <YENİ klasör> --box x0 z0 x1 z1 --preset <en yakın> [--set ...]`
(MCP: `edit_world`); asla kaynak üstüne ya da saves içine yazma, sonucu kullanıcı kendisi kopyalar.

Model gerektirmeyen işler: aynı haritayı yeniden üretme, seed değiştirme, export, doğrulama, test.
Model gerektiren işler: yeni tasarım kararı, yeni preset, motor geliştirme, görsel kalite değerlendirmesi.

## Token kaydı
`logs/token_usage.jsonl`: yalnızca sağlayıcının verdiği gerçek sayılar yazılır. Claude Code oturumu
çağrı başına input/output sayısı vermiyor; bağlam penceresi doluluğu ölçülebiliyor ve o kaydedildi.

## MCP ile kullanım
Sunucu: `agent/mcp_server.py` (stdio, kökteki `.mcp.json` ile Claude Code'a kayıtlı). Araçlar tek satır JSON döner, dizi/dosya içeriği dönmez:
- `list_presets()` → ad: tek satır açıklama
- `describe_params()` → `numeric` {ad: [varsayılan, min, max]}, `choices` {ad: [seçenekler]}
- `generate(preset, overrides, export_world=False, verify=False, force=False, tile=None)` — `tile` boşsa 1024²'den büyük haritalarda otomatik 1024 (çıktı aynı, bellek düşük) → out, cache_hit, cache_key, yükseklik min/max, rock_pct, tree_pct, seconds (+ world/verify). `overrides` motora aynen geçer (yeni parametreler, ör. `regions`, otomatik çalışır).
- `preview(export_dir, kind="oblique"|"topdown")` → ≤768 px PNG + yol
- `server_load_test(export_dir)` → yerel MC 1.21.4 sunucu testi sonucu (yavaş)
- `install_world(export_dir, new_name=None)` → `%APPDATA%\.minecraft\saves\<yeni ad>`; var olanın üzerine yazmaz.
- `edit_world(source, out, box=[x0,z0,x1,z1], preset, overrides, blend=24)` → var olan dünyanın kutusunu yeni `out` kopyasında düzenler (edit-world raporu).

Düşük token akışı: `list_presets` → `generate` → (gerekirse) `preview` tek görsel. `cache_hit: true` ise tekrar üretme/doğrulama yok.

### Canlı oyun (bridge, Faz 11)
- `ingame_tour(export_dir, views=None)` → tek çağrı: oyun kapalıysa açar, dünyanın kopyasını açar, otomatik 4-6 görüntü
  (overview, river, mountain, coast, meadow, cave) alır, dünyadan çıkar, kendisi açtıysa oyunu kapatır. Dönüş: 512 px JPEG'ler +
  tek satır JSON (yol, `settled`, chunk sayısı). Tam boy PNG'ler `previews/ingame/`. `views`: `[{name,x,y,z,yaw,pitch}]`
  (yaw 0 = güney/+z, 90 = batı; pitch + = aşağı).
- Birden fazla tur/komut için: `game_start()` → `ingame_tour(...)` / `game_command("...")` → `game_stop()`. Oyunu açık bırakma.
- `game_status()` ucuz durum kontrolü. `game_command` yalnız bir dünya açıkken çalışır (op seviyesi, çıktı = geri bildirim satırları).
- Düşük token: çevrimdışı `preview` yetiyorsa oyunu açma; oyun içi kontrol gerekiyorsa tek `ingame_tour`, yalnız gereken görseli
  değerlendir. İlk açılış dakikalar sürer (indirme), sonraki ~16 sn.
