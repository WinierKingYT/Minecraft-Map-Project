# Entegrasyon notları (doğrulanmış)

## WorldPainter 2.27.1 (headless)
- `wpscript.exe <script.js>` (Nashorn JS). Kullanılan API: `getHeightMap`, `createWorld().fromHeightMap()
  .fromLevels(0,65535).toLevels(0,320)`, `applyHeightMap().applyToTerrain()/applyToLayer()`, `saveWorld`, `exportWorld`.
- Map format: `org.pepsoft.anvil.1.20.5` ("Minecraft 1.20.5 - 1.21.10"); dünya DataVersion 3837 yazılır, 1.21.4 açarken yükseltir.
- **Türkçe locale hatası:** WP, Türkçe sistemde `region` → `regıon` yazar; Minecraft dünyayı boş görür.
  Çözüm: `-Duser.language=en -Duser.country=US` (vmoptions dosyalarına eklendi; köprü `JAVA_TOOL_OPTIONS` da veriyor).
  WorldPainter GUI'yi de bu portable kopyadan aç.
- WP 1.20.5 formatı (DataVersion 3837) çimeni eski adla `minecraft:grass` yazar; `terrain_engine/anvil_fix.py`
  export sonrası region dosyalarında `short_grass` olarak düzeltir.
- `toTerrain(i)` indeksi `Terrain.VALUES` sırasıdır, enum ordinal değil — köprü isimle çözüyor.
- Katman isimleri (`wp.getLayer().withName()`, 2.27.1 probu): `Deciduous`, `Pine`, `Jungle`, `Swamp`, `Resources`,
  `Caverns`, `Chasms`, `Caves` (4-bit), `Frost`, `Void` (1-bit), `Biomes` (BYTE, doğrudan eşleme), `Populate`, `Annotations`.
  Yok: `Biome`, `River`, `Flowers`, `Grass`, `Ferns`, `Plants` — bitki katmanı (`PlantLayer`) yalnız özel katman.
- Biyom ID'leri (1.20.5 formatı): `Minecraft1_21Biomes.BIOMES_BY_MODERN_ID`; eski biyomlar klasik sayısal ID
  (ocean 0, plains 1, windswept_hills 3, forest 4, taiga 5, river 7, beach 16, stony_shore 25, birch_forest 27, dark_forest 29),
  1.18+ biyomlar 246..254 (cherry_grove 246, frozen_peaks 249, grove 250, jagged_peaks 251, meadow 252,
  snowy_slopes 253, stony_peaks 254). 255 = otomatik. Varsayılan eşlemede siyah (0) atlanır → her değer açıkça eşlenmeli.
- Caverns/Chasms: script dünyasında katman ayarı yok (null) → varsayılan `CavernsSettings` (surfaceBreaking=false,
  leaveWater=true, min/max sınırsız). Yoğunluk eşik gibi: 6 → kolonların %1.5'i, 10 → %12, 15 → %18 oyuldu.
- Filtre: `wp.createFilter().exceptOnWater().go()` (belgedeki açıklama karışık, ama `exceptOnWater` doğru çalışıyor).
  Filtreyi ve katmanı `applyHeightMap()` başlamadan oluştur ("You forgot to invoke go()" hatası).
- Kenar dikişi çözüldü (motor 0.2.0; 0.9.0'da derinleşti): `edge_falloff` (blok) haritayı gürültülü bir kıyıyla
  `water_level-10` (`SEA_DEPTH`) deniz tabanına indirir; `outside: "ocean"` dışa aktarımdan sonra level.dat'taki overworld
  jeneratörünü düz okyanusa çevirir (bedrock/taş/4 kum/10 su, yüzey = water_level). Orijinal level.dat `level.dat.bak` olarak kalır.
  `outside: "vanilla"` eski davranış (harita dışı normal Minecraft arazisi).

- **Büyük export (Faz 6, ölçüldü):** 4096² (65 536 chunk, 64 region) tek `exportWorld` çağrısıyla sorunsuz; parçalı export gerekmedi.
  Süre ~3 dk; `-Xmx3g` ile de geçti (heap ~alanla ölçeklenir; 8192² için ~12 GB tahmini, ölçülmedi). Ayar yalnız portable kopyanın
  `wpscript.vmoptions` dosyasında (`-Xmx6g`, 4096² için 2× pay; `-Xmx` yüksekse JVM çalışma kümesini ona kadar büyütür: 12g ile 10.3 GB görüldü); WP Java'yı sistemden alır (Temurin 25, varsayılan heap = RAM'in %25'i).
  `.world` dosyası 35 MB. WP export dizininde boş `backups/` klasörü açabiliyor (zararsız).
  WP çıktısı çalıştırmadan çalıştırmaya birebir değil (ör. `minecraft:grass` palet sayısı 65 592 / 65 554) — WP'nin kendi rastgeleliği.
- WP heightmap/katman girdileri tam çözünürlüklü PNG (4096²: 16 bit yükseklik 16 MB).
- **Kolon başına su seviyesi (Faz 9, probla doğrulandı):** script API'sinde (`MappingOp`) su seviyesi eşlemesi yok
  (`applyToLayer/Terrain/Surface/...` var, su yok). Ama `Dimension.setWaterLevelAt(x, y, level)` (GUI'nin Flood aracının
  yazdığı) public Java metodu: `world.getDimension(Dimension$Anchor.NORMAL_DETAIL)` ile alınıp çağrılır. Seviye = üst su
  bloğunun y'si; WP o kolonda zeminden seviyeye dek su doldurur ve su altındaki çimeni toprağa çevirir. Deneme: 32² çanak,
  seviye 69 → y 67..69 su, kenarlar kuru. Köprü yalnız su kolonlarını `water_level.txt` (`x z seviye`) olarak verir,
  JS `java.nio.file.Files.readAllLines` ile okur (1024²: 15 100 satır, ek süre ihmal edilebilir).
- **Bitki/obje katmanları:** `PlantLayer`, `Bo2Layer` (bo2/bo3/schem/nbt nesneleri) `CustomLayer`; script API'sinde kurucu yok,
  yalnız `wp.getLayer().fromFile('x.layer')` ile GUI'de kaydedilmiş `.layer` (Java serileştirmesi) yüklenir. Belgesiz
  kurucuları çağırmadık; bunun yerine `terrain_engine/decorate.py` export sonrası blokları kendisi yazar (region dosyalarını
  `edit_world` yardımcılarıyla). Arazide doğal kapalı çukur olmadığı için göller kazınmış çanaklarda (bkz. CHANGELOG 0.11.0).

## Axiom 6.1.5 (MC 1.21.4, Fabric)
- Bağımlılık: fabricloader ≥0.14.21 (kurulu 0.19.5), fabric-api (0.119.4), Java ≥17.
- `breaks: opsec, optifabric, clientspoofer, sodium≤0.5` → mevcut mods klasöründeki `opsec` ile uyumsuz. Bu yüzden ayrı mod seti
  (`axiom/instance/mods`) ve geri alınabilir `scripts/axiom_mods_on/off.ps1`.
- Dışarıdan otomasyon API'si **yok** (resmî belge yok). Script Brush Lua presetleri `axiom/scripts/` içinde; oyunda Tool Options'a yapıştırılır.
  Belgelenen fonksiyonlar: getBlock/setBlock/getHighestBlockYAt/isSolid/getSimplexNoise/getVoroniEdgeNoise, `$int/$float/$blockState/$once$`.
- Presetler oyun içinde henüz çalıştırılmadı (oturum açılmış istemci gerektirir).

## Canlı istemci köprüsü (Faz 11, doğrulandı)
- Sürümler (fabric-example-mod `1.21.4` dalı, Kasım 2026): Gradle 9.8.2 wrapper, Loom `1.19-SNAPSHOT` (çözülen 1.19.4,
  eklenti kimliği `net.fabricmc.fabric-loom-remap` — Yarn remap için), Yarn `1.21.4+build.8:v2`, Loader 0.19.5,
  Fabric API `0.119.4+1.21.4`. Örnek mod artık Mojang eşlemesi kullanıyor; biz Yarn kullandık.
- **Loom 1.19, Gradle'ı JDK ≥25 ile çalıştırmak ister.** Sistemde `JAVA_HOME` = JDK 21 → "requires at least JVM runtime version 25"
  hatası. Sürücü `JAVA_HOME`'u PATH'teki ilk `java`dan (Temurin 25) türetir; `BRIDGE_JAVA_HOME` ile ezilebilir.
  Oyun da JDK 25'te sorunsuz çalıştı (yalnız `sun.misc.Unsafe` uyarıları). JDK 21 indirmeye gerek olmadı.
- İndirilen (resmî kaynaklar): wrapper betikleri/jar GitHub FabricMC şablonundan (jar SHA-256 = services.gradle.org), Minecraft 1.21.4
  + kütüphaneler Mojang'dan (~143 MB loom önbelleği, ~100 MB yeni asset), Fabric/Yarn maven.fabricmc.net'ten (~70 MB). Gradle 9.8.2
  dağıtımı `~/.gradle` içinde zaten vardı. `run/` ~32 MB (0.9.0'dan beri `tour()` görüntüyü `run/screenshots`'tan taşır, birikmez).
- Yarn API'leri: `MinecraftClient.submit(Supplier)`, `createIntegratedServerLoader().start(name, onCancel)`,
  `getLevelStorage().getSavesDirectory()`, `worldRenderer.getCompletedChunkCount()/isTerrainRenderComplete()`,
  `ScreenshotRecorder.saveScreenshot(runDir, "ad.png", getFramebuffer(), cb)` (asenkron yazar; geri çağrı beklenir),
  `world.disconnect()` + `disconnect(new MessageScreen(...))`, `options.hudHidden`, `getWindow().setWindowedSize()`,
  `ServerPlayerEntity.getCommandSource().withOutput(sink).withLevel(4)` + `getCommandManager().executeWithPrefix()` (sunucu iş parçacığında),
  `ClientTickEvents.END_CLIENT_TICK` (tick sayacı). HTTP: JDK `com.sun.net.httpserver` (ek bağımlılık yok).
- Notlar: 1.21.4 eski (DataVersion 3837) dünyayı onay ekranı olmadan açtı. Açılışta "key missing: DragonFight" (zararsız,
  End verisi yok) çıkıyor. **"Not a string" çözüldü (0.9.0):** WP 2.27.1 level.dat'ta `Data.Player` içine `SpawnX/Y/Z` yazıyor ama
  `SpawnDimension` (ve `Dimension`) yazmıyor; 1.21.4 oyuncuyu yüklerken eksik boyut anahtarını string olarak çözmeye çalışıp bu
  ERROR'u basıyor (varsayılan overworld'e düşüyor). `worldpainter.fix_player_dimension` ikisini `minecraft:overworld` olarak ekliyor;
  düzeltilmiş dünyalarda satır yok. Kaynak WP; bizim `set_outside_ocean` değişikliğiyle ilgisi yok. Spektatör blok içindeyse x-ray gibi
  görünür → mağara kamerası dünyadaki gerçek hava boşluğuna konur (`find_cave`) + gece görüşü. Bulutlar (y≈192) genel bakışı
  kapattığı için `renderClouds:"false"`. Kamera = `gamemode spectator` + `tp @s x y z yaw pitch`.

## Bridge / MCP tasarımı (öneri)
Axiom kontrol edilemediği için agent'ın Minecraft'a erişimi üç katman:
1. **Çevrimdışı** (çalışıyor): motor → WP → dünya dosyası; NBT okuyucu ile doğrulama.
2. **Sunucu** (çalışıyor, test aracı olarak): resmi 1.21.4 sunucu 127.0.0.1'de; stdin komutları (`execute if block`, `fill`, `forceload`).
   MCP aracı olarak sarılabilir: `load_world`, `probe_blocks`, `apply_region(params, box)`.
3. **Canlı istemci** (Faz 11'de yapıldı, yukarıya bkz.): Fabric dev istemcisi + localhost HTTP köprü modu → dünya aç, kamera, ekran görüntüsü, komut.
   Axiom yalnızca insan düzenlemesi için kalır.

## Minecraft sunucu yükleme testi
`python scripts/mc_server_load_test.py <export_dir>` — dünyanın kopyasını yükler, rastgele 12 ağaçsız kolonda
yüzey bloğu katı + üstü hava mı, harita dışı ve sınırda 5 noktada su yüzeyi + katı deniz tabanı var mı kontrol eder. EULA kullanıcı onayıyla kabul edildi (2026-11-19).
