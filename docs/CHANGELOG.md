# CHANGELOG

## 0.9.0 — 2026-11-19 (Faz 7: oyun içi kontrol)
- Güvenlik: `name` yalnız `[A-Za-z0-9_-]{1,64}` (exports altında rmtree edilen klasör adı; yol/`..` reddedilir). MCP `edit_world` çıktısı yalnız `exports/edits/<ad>`; kaynak `level.dat` içermeli.
- Oyunda görülen: yamaçlar çıplak gri taş + düzenli yatay çizgiler (WP `Rock`/`Stone mix` yüksekliğe göre katmanlı karışım),
  dağlar 1 bloklu kum şeritle denize duvar gibi iniyordu, deniz her yerde 3 blok derin, her açılışta "Not a string" hatası.
- Kaya: yalnız saf bloklar, gürültü lekeleriyle (`_rock_mix`: taş, geniş andezit bölgeleri, tüf, çakıl cepleri); `ROCK`/`STONE_MIX`
  artık yüzeyde kullanılmıyor. Kaya eşiği yükseldi (`60 - 16·rock_ratio`, ince gürültüyle pürüzlü kenar) → çimen/kaba toprak/podzol
  yamaçlara tırmanıyor; açık yamaçta toprak lekeleri, kaya içinde toprak cepleri; eğimli çimen çıkıntılarında seyrek ağaç.
  Kara içinde kaya oranı: fantasy %35→%15, mountain_valley %45→%24 (256²).
- Kıyı: `SEA_DEPTH` 3→10; harita dışı düz okyanus 10 su (sunucu testi yoklamaları da). `_cap_coast`: kenardan içeri yükselen
  gürültülü bir tavan (yumuşak min) — bazı kesimlerde ~14° (kumsal), bazılarında ~72° (kayalık kıyı). `_shelf`: deniz tabanı kıyı
  bandının dışında hızla yükselir, kara içte → alçak kıyılar yumuşak. Kıyı yüzeyi: dik = kaya/çakıl (`stony_shore` biyomu, ID 25),
  yumuşak = kum + çakıllı kumsal; deniz tabanı kum/çakıl/kil lekeleri. Kıyı şeridi (su ±2) eğim medyanı fantasy 47→29°, mountain_valley 51→30°, rocky 58→32°.
  Bedel: 256² haritada kara %85→%72; 128² gibi küçük haritada kenara yakın zirveler alçalır.
- "Not a string": WP level.dat'ında `Player.SpawnDimension`/`Dimension` yok → `fix_player_dimension` ekliyor (her export'ta).
- Köprü: `tour()` ekran görüntüsünü `bridge/run/screenshots`'tan taşır (birikmez); `find_cave` aramayı istenen pencereyle sınırlar
  (chunk yuvarlaması 32+15 blok uzağa kaçırıyordu); otomatik mağara kamerası en büyük mağara lekesinin ortasında.
- Testler: v0.3/v0.4 "özellik kapalı" özetleri v0.9.0 ile yeniden kaydedildi (yüzey/kıyı değişikliği kasıtlı); `test_alpine_biomes`
  ve bölge dikiş testi `edge_falloff=0` (kıyı tavanı ölçümü karıştırıyordu); `fix_player_dimension` testi eklendi.
- Önce/sonra oyun içi görüntüler: `previews/phase7/` (fantasy_valley, mountain_valley, rocky_cliffs; 256², seed 20261118).

## 0.8.0 — 2026-11-19 (Faz 11: canlı istemci köprüsü)
- `bridge/`: Fabric Loom geliştirme projesi (MC 1.21.4, Yarn 1.21.4+build.8, Loader 0.19.5, Fabric API 0.119.4+1.21.4,
  Loom 1.19-SNAPSHOT, Gradle 9.8.2 wrapper — fabric-example-mod 1.21.4 dalından; wrapper jar SHA-256 Gradle'ın yayınladığıyla aynı).
  `gradlew runClient` resmî oyun dosyalarıyla geliştirme istemcisini açar (dev kullanıcı adı, hesap yok); oyun klasörü `bridge/run`.
- Köprü modu `terrainai.bridge.BridgeMod` (yalnız istemci): `127.0.0.1:25580` HTTP, rastgele jeton `bridge/run/bridge_token.txt`
  (`X-Bridge-Token` başlığı, sabit-zamanlı karşılaştırma), loopback dışı istek 403, gövde ≤64 KB. Uç noktalar: `GET /status`,
  `POST /open_world /command /camera /screenshot /quit_world /shutdown`. Komutlar entegre sunucuda op-4 kaynakla, çıktı yakalanır.
  Render "oturdu" = chunk builder boş + tamamlanan chunk sayısı 2 sn sabit.
- `agent/bridge_client.py`: oyunu başlat/durdur (zaman aşımlı; zarif kapanmazsa süreç ağacı öldürülür), `options.txt` ayarları
  (görüş 16, bulut kapalı, ses 0, odak kaybında durma yok), `auto_viewpoints` (genel bakış 60°, nehir, dağ→vadi, kıyı, çayır,
  mağara) + `find_cave` (dışa aktarılan dünyada gerçek mağara havası), `tour()` / `tour_export()`; dünya `bridge/run/saves` içine
  yeni adla kopyalanır (`allowCommands=1`), tur sonunda kopya silinir.
- MCP: `game_start`, `game_status`, `game_command`, `game_stop`, `ingame_tour` (512 px JPEG küçük resimler + yollar). Jeton hiçbir çıktıda yok.
- Ölçülen: ilk açılış 264 sn (indirme dahil), sonraki açılış ~16 sn; 6 görüntülük tur ~40 sn. `tests/test_bridge.py` (20 hızlı + `RUN_GAME=1`).

## 0.7.0 — 2026-11-19 (Faz 6: büyük MMO haritaları)
- MCP `generate`: `tile` parametresi (büyük haritada otomatik 1024; çıktı parçasız üretimle birebir aynı, testli).
- Hidrolik erozyon ölçek-duyarlı: kaba hücre `cell_size()` = 2 blok (≤256²), harita her iki katına çıktıkça 2× (en çok 16;
  512²→4, 1024²→8, ≥2048²→16); kaba ızgaradaki şev açısı `0.8·(2/cell)^0.7`. 256² çıktısı değişmedi (hücre 2);
  mountain_valley arazi eğimi medyan/p90 (kapalı → 0.6 → 0.7): 1024² 15.0/33.1 → 20.4/50.2 → 12.8/28.1;
  4096² 10.4/27.9 → 8.8/42.4 → 8.0/20.5; 512² 24.5/43.6 → 22.3/43.1. `hydraulic=0` çıktısı bit-bit aynı (v0.3.0 hash testi).
- Global geçiş artık tam çözünürlüklü ön-yüksekliği bellekte tutmuyor: 512'lik parçalarla (margin sayesinde birebir)
  hesaplayıp yalnız kaba ortalamaları saklıyor; önbellekte yalnız kaba delta. Parçalar yine bit-bit aynı.
- Paralel üretim: harita ≥1024² ise global geçiş ve `--tile` parçaları işçi süreçlerde (`WORKERS` = çekirdek/2, en çok 8);
  `build()` parçaları doğrudan tam boy float32/uint8 alanlara yazar (parça listesi tutulmaz). `_thermal` gereksiz `np.pad`'siz (aynı bitler).
- Önizlemeler: en çok 1024 px (büyük haritada adımlı örnekleme, rölyef √adım ile ölçekli); oblique 4096²'de 0.1 sn.
- `verify_world`: bölge bölge akışlı; tüm region başlıklarından chunk sayısı + en çok 9 bölgenin (eşit aralıklı, deterministik)
  blok düzeyi kontrolü. Yeni alanlar: `regions_verified`, `chunks_in_region_files`, `map_chunks`, `columns_verified`.
- `anvil_fix`: region başına akış; yalnız eski adı içeren chunk'lar çözülüp yeniden yazılır, region'lar paralel işlenir.
- WorldPainter: `wpscript.vmoptions` içinde `-Xmx6g` (4096² `-Xmx3g` ile de geçti); köprü zaman aşımı alanla ölçekli (15 dk + 1 sn/10k kolon).
- Sunucu testi: 4×4 ızgaranın her hücresinden bir kuru/ağaçsız kolon, her yoklama yalnız kendi chunk'ını forceload eder (büyük haritada 256 chunk sınırı yok).
- `python -m terrain_engine` işçi süreçler için `__main__` korumalı. `tests/test_scale.py` (7 hızlı + 1 `RUN_SLOW=1`).

## 0.6.0 — 2026-11-19
- Sunucu testi: her koşul ayrı `execute` komutu (zincirde ilk koşul tutmazsa Minecraft hiç çıktı vermiyor, hata gizleniyordu); üstteki blok `#minecraft:replaceable` olabilir (kısa çimen).
- Mevcut haritayı düzenleme: `python -m terrain_engine edit-world --source --out --box x0 z0 x1 z1 --preset [--set] [--blend 24]`,
  `terrain_engine/edit_world.py`, MCP aracı `edit_world`. Yalnız kopyada: out var/kaynakla aynı/kaynağın içi/`%APPDATA%\.minecraft`
  altı ise ret; kaynak salt okunur, kopya ancak tüm kontroller ve hesap bitince yapılır.
- Okuma: kutu (+chunk payı) tek blok-durumu ızgarasına; zemin sınıflaması verify ile aynı; kolon başına üst zemin, yüzey bloğu, su üstü.
- Düzenleme: motor kutu-yerel üretir (`edge_falloff`=0); dış 5 blok sabit, sonra `blend` boyunca smoothstep. Yeniden kurulan
  kolon: terrain sınıfına göre üst blok (grass_block/podzol/coarse_dirt/moss_block/gravel/sand/clay/stone/andesite/calcite/snow_block),
  3 alt blok, altı taş; üstü hava. Su: eski su yüzeyi korunur, yeni zemin `water_level` altındaysa zeminden itibaren doldurulur
  (havada su yok); su altında çimen yerine toprak. Yeni ağaç konmaz; kutuda zemine bağlı kütüğe 26-komşulukla bağlanmayan kütük/yaprak silinir.
- Yazma: yalnız değişen bölümler yeniden paketlenir (bits=max(4,⌈log2 n⌉), long sınırını aşmaz); değişen chunk'ta
  `Heightmaps` boş, `isLightOn`=0, bölüm ışıkları silinir (oyun yeniden hesaplar), değişen bloktaki blok varlıkları düşer.
  Değişmeyen chunk'ların sıkıştırılmış blob + zaman damgası, diğer region/entities dosyaları birebir.
- `scripts/mc_server_load_test.py --world <dünya> --box ...`: düzenlenmiş dünyada yüzey bloğu yoklaması. `ENGINE_VERSION` değişmedi.
- `tests/test_edit_world.py` (ret, kaynak hash, iç hedef ±1, kutu dışı diff, dikiş, yüzen ağaç/su, paketleme gidiş-dönüş).

## 0.5.0 — 2026-11-19
- Export sonrası `anvil_fix`: WP'nin yazdığı eski `minecraft:grass` → `minecraft:short_grass` (1.20.3+'da eski ad yok sayılıyordu).
- Mağaralar: yeni global parametre `caves` (0..1, vars. 0) → WorldPainter `Caverns` + `Chasms` katmanları
  (`caverns.png`/`chasms.png`, 0..15). Maske global koordinattan; nehir ±8 blok, kıyı bandı ve düşük kara boş;
  WP'de ayrıca `exceptOnWater` filtresi. WP ~8'in altındaki yoğunlukta neredeyse oymuyor → `caves` hem alanı
  hem 8..15 yoğunluğu belirler. WP varsayılanı yüzeyi delmez (surfaceBreaking=false).
- Biyomlar: `biomes` (`off`/`on`, vars. off) → `biomes.png` (WP biyom ID'si) WP `Biomes` katmanına değer-değer
  (`fromLevel(v).toLevel(v)`, 0=ocean atlanmasın diye) uygulanır: nehir, okyanus/kumsal, forest, birch/dark forest
  (fantasy), taiga (çam ağırlıklı), meadow/stony_peaks (ağaç sınırı üstü), snowy_slopes/jagged_peaks (kar).
- Bitki çeşitliliği: atlandı — WP'de varsayılan bitki katmanı yok (`PlantLayer` özel katman; belgelenmemiş
  kurucu ya da GUI'de yapılmış `.layer` dosyası gerekir). Ağaç katmanları zaten çimen/eğrelti/birkaç çiçek koyuyor.
- `caves`/`biomes` global (bölgede reddedilir). Presetler: hepsinde `biomes: on`; `caves` fantasy 0.25,
  mountain_valley 0.3, rocky_cliffs 0.4, west_forest_east_rocky 0.2.
- verify: `cave_air_pct`, `cave_columns_pct`, `cave_air_under_river_blocks`, `biome_pct`,
  `river_core_is_river_biome_pct`, `biome_match_pct` (chunk NBT `biomes` paletinden).
- Kapalıyken çıktı 0.4.0 ile bit-bit aynı (7 preset hash testi). `tests/test_features.py`.

## 0.4.0 — 2026-11-19
- Hidrolik erozyon: yeni global parametre `hydraulic` (0..1, vars. 0) + `terrain_engine/hydraulic.py`.
  Damla erozyonu sıraya bağlı ve yerel değil; bu yüzden tüm haritanın termal-sonrası yükseklikleri bir kez
  2 blok/hücre ızgarada aşındırılır, fark alanı süreç içinde önbelleklenir ve her parça onu global koordinatta
  bilineer örnekler → parçalı üretim bit-bit aynı kalır. Nehir oyma, iç kara kelepçesi ve kıyı sönümü sonra uygulanır.
- Damlalar numpy ile toplu (vektörel) yürür; numba gerekmedi (256² +0.5 sn, 1024² +9 sn).
  Kıvrımlar: kapasite yol boyunca artar (sırtlar az aşınır), zirveler sert kaya (aşınabilirlik düşer),
  her toplu adımdan sonra kısa termal gevşeme (duvarlar ~39°), kıyı bandı aşınmaz.
- Presetler: mountain_valley 1.0, fantasy_forest_valley 1.0, river_valley 0.7, rocky_cliffs 0.5. forest_hills ve
  west_forest_east_rocky kapalı (tepelerde dere oyması eğimi artırıyordu).
- `hydraulic=0` iken çıktı 0.3.0 ile bit-bit aynı (hash testi). `tests/test_hydraulic.py`.

## 0.3.0 — 2026-11-19
- Bölgesel düzenleme: `regions` (half/box/circle/polygon, `blend`, `preset` + `set`), `terrain_engine/regions.py`;
  sayısal parametreler hücre hücre harmanlanır, `biome` baskın ağırlıkla; global parametreler bölgede reddedilir.
  `regions` yoksa çıktı 0.2.0 ile bit-bit aynı. Yeni preset `west_forest_east_rocky`; `tests/test_regions.py`.
- MCP: `agent/mcp_server.py` (stdio; list_presets, describe_params, generate, preview, server_load_test, install_world), `.mcp.json`.

## 0.2.0 — 2026-11-19
- Kenar sönümü (`edge_falloff`) ve harita dışı düz okyanus (`outside: "ocean"`, level.dat jeneratör yaması).
- Sunucu testi: harita dışı/sınır yoklamaları, forceload sınırı düzeltmesi, watchdog.

## 0.1.0 — 2026-11-18
- Perlin/fBm/ridged/warp noise, vadi + kıvrımlı nehir, 8-komşu termal erozyon, yüzey ve ağaç maskeleri.
- 6 preset, JSON parametre + doğrulama, içerik-hash cache, parçalı üretim (bit-bit aynı).
- WorldPainter 2.27.1 headless export (Türkçe locale düzeltmesi), NBT tabanlı dünya doğrulama, pytest.
- Axiom 6.1.5 ayrı mod seti + 4 Lua Script Brush preset.
