# Minecraft-Terrain-AI

Parametrik, deterministik arazi motoru → WorldPainter (headless) → Minecraft Java 1.21.4 dünyası.
AI yalnızca JSON parametresi seçer; blok hesabı yerel Python/Java'da yapılır.

## Hızlı kullanım

```powershell
.\.venv\Scripts\python -m terrain_engine presets
.\.venv\Scripts\python -m terrain_engine generate --preset fantasy_forest_valley --set seed=20261118 --set name=fantasy_valley_256 --verify
.\.venv\Scripts\python -m pytest
```

- `--set key=value` herhangi bir parametreyi ezer (tam liste ve sınırlar: `terrain_engine/params.py`).
- `--params my.json` parametreleri dosyadan okur.
- `--verify` WorldPainter ile dünyayı dışa aktarır ve dünyayı bağımsız NBT okuyucuyla kontrol eder.
- `--tile 1024` büyük haritaları parça parça üretir (parçalar tam haritayla bit-bit aynıdır, testli); ≥1024² haritalarda
  parçalar paralel işçi süreçlerde üretilir. Ayrıntı: aşağıda "Büyük haritalar".
- Aynı parametre + motor sürümü → `CACHE HIT`, yeniden hesaplama yok.
- `hydraulic` (0..1, vars. 0 = kapalı): damla tabanlı hidrolik erozyon — dereler/yarıklar, yamaç diplerinde alüvyon,
  daha düşük medyan eğim. Tüm haritada tek global geçiş, kaba hücre harita boyutuyla büyür (256²: 2 blok … ≥2048²: 16 blok),
  parçalar yine bit-bit aynı.
  Dağlık presetlerde açık (mountain_valley/fantasy_forest_valley 1.0, river_valley 0.7, rocky_cliffs 0.5).
- `caves` (0..1, vars. 0): WorldPainter Caverns + Chasms mağaraları (nehir/deniz altında yok, yüzeyi delmez).
- `biomes` (`off`/`on`, vars. off; presetlerde on): nehir/okyanus/kumsal/orman/taiga/zirve biyomları WP Biomes katmanıyla.
  Her ikisi global (bölgede kullanılamaz). verify mağara havası ve biyom dağılımını raporlar.

### Büyük haritalar (4096² ve üstü)

```powershell
.\.venv\Scripts\python -m terrain_engine generate --preset mountain_valley --set width=4096 --set length=4096 `
    --set seed=20261118 --set name=mmo_4096 --tile 1024 --verify
.\.venv\Scripts\python scripts\mc_server_load_test.py exports\mmo_4096-<cachekey>
```

- Ölçülen (Ryzen 7 7800X3D, 31 GB): üretim ~5 dk, tepe RAM ~2.9 GB (ana süreç + 8 işçi); WorldPainter export ~3 dk
  (`-Xmx3g` yetiyor; `tools/worldpainter/WorldPainter/wpscript.vmoptions` içinde `-Xmx6g`); anvil_fix 16 sn; verify 22 sn;
  dünya 496 MB (64 region). Toplam ~11 dk. Daha az RAM için `--tile 512` (işçi başına bellek düşer, süre biraz artar).
- Önizlemeler en çok 1024 px (adımlı örnekleme). `heightmap16.png` vb. WP girdileri tam çözünürlüktür.
- `verify` büyük dünyada tüm region başlıklarını sayar, blok düzeyinde 9 bölgeyi (eşit aralıklı) okur.
- Sunucu testi 4×4 ızgarada yoklar; her yoklama yalnız kendi chunk'ını yükler.
- 8192²+: kod sınırı yok (kaba ızgara en çok 16 blok/hücre, hafıza parça başına); WP heap'i yaklaşık alanla büyür (ölçülmedi).

### Bölgesel düzenleme (`regions`)

Preset ya da `--params` dosyasına `regions` listesi eklenir; sonraki bölge öncekinin üstüne yazılır:

```json
"regions": [
  {"shape": "half", "side": "west", "blend": 32, "preset": "forest_hills", "set": {"tree_density": 0.9}},
  {"shape": "half", "side": "east", "blend": 32, "preset": "rocky_cliffs"}
]
```

- Şekiller (blok koordinatı, x=doğu, z=güney): `half` (`side`: west/east/north/south), `box` (`x0,z0,x1,z1`),
  `circle` (`cx,cz,r`), `polygon` (`points: [[x,z],...]`). `blend`: sınırdaki yumuşak geçiş genişliği (vars. 32).
- `preset`: o preset'in harmanlanabilir değerleri; `set`: tek tek değerler (preset'in üstüne).
- Harmanlanabilir: `base_height, mountain_height, roughness, hill_amplitude, warp_strength, ridge_sharpness,
  cliffiness, terrace_step, tree_density, forest_coverage, rock_ratio, soil_ratio, snow_line, tree_line, pine_line`
  (hücre hücre doğrusal) ve `biome` (baskın ağırlık). Diğerleri (boyut, seed, su, octaves, noise_scale, erosion,
  hydraulic, caves, biomes, nehir/vadi, edge_falloff) global'dir; bölge içinde verilirse hata.
- Örnek: `generate --preset west_forest_east_rocky --set seed=20261118 --set name=west_forest_east_rocky --verify`.

### Mevcut haritayı düzenleme (`edit-world`)

Var olan bir 1.21.x dünyasının bir kutusunu motorla yeniden şekillendirir; sonuç **yeni bir klasöre** yazılır:

```powershell
.\.venv\Scripts\python -m terrain_engine edit-world --source D:\yedek\dunyam --out D:\yedek\dunyam_v2 `
    --box 64 64 192 192 --preset forest_hills --set seed=7 --blend 24
.\.venv\Scripts\python scripts\mc_server_load_test.py --world D:\yedek\dunyam_v2 --box 64 64 192 192
```

- `--box x0 z0 x1 z1`: blok koordinatı (x1/z1 hariç, en fazla 512). Kutunun dış 5 bloğu dokunulmaz (ağaç güvenliği),
  sonra `--blend` blokta eski yükseklikten yeni araziye smoothstep geçiş, içi tamamen motor arazisi.
- Motor kutu-yerel koordinatta çalışır (`regions` koordinatları da kutuya göre); `edge_falloff` kutuda 0'a çekilir.
- Güvenlik: kaynak yalnız okunur; `--out` var olan klasör, kaynağın içi/kendisi veya `%APPDATA%\.minecraft` altı ise reddedilir.
  Oyuna almak için çıktıyı `saves` klasörüne kendin kopyala.
- Sınırlar: yeni ağaç/bitki konmaz; biyomlar ve entity'ler (`entities/`) değişmez; ışık ve heightmap oyunca yeniden hesaplanır;
  kolon yeni yüzeyin 3 blok altına kadar yazılır, daha derindeki mağaralar olduğu gibi kalır (alçaltılan yerde tavan
  açılabilir, kum/çakıl düşebilir); yeni su kutu kenarında taşabilir (havada asılı su yok).
- Çıktı tek satır JSON: düzenlenen kolon/chunk sayısı, yükseklik değişimi, `interior_max_err`, `edge_jump_added_max`,
  temizlenen ağaç blokları, silinen blok varlıkları.

Çıktılar: `exports/<name>-<cachekey>/` → `heightmap16.png`, `terrain_index.png`, `trees_*.png`,
`params.json`, `manifest.json`, `previews/`, `worldpainter/*.world`, `world/<name>/` (Minecraft dünyası),
`verification.json`, `server_load_test.json`.

## Canlı oyun köprüsü (Faz 11)

Ajan Minecraft'ı kendisi açar, test dünyasını açar, kamerayı konumlar ve ekran görüntüsü alır:

```powershell
.\.venv\Scripts\python agent\bridge_client.py exports\fantasy_valley_256-<cachekey>   # başlat → tur → kapat
```

- Oyun = `bridge/` içindeki Fabric Loom projesinin `gradlew runClient` geliştirme istemcisi (resmî dosyalar, dev kullanıcı adı,
  hesap yok). Oyun klasörü `bridge/run`; TLauncher ve `%APPDATA%\.minecraft`'a dokunulmaz. Gradle için JDK ≥25 gerekir
  (PATH'teki Temurin 25 otomatik; başka yer için `BRIDGE_JAVA_HOME`). İlk açılış indirmeyle ~4-5 dk, sonra ~16 sn.
- Dünya `bridge/run/saves` içine yeni adla kopyalanır (komutlar açık), görüntüler `exports/<..>/previews/ingame/*.png` (1280×720).
- Köprü yalnız `127.0.0.1:25580`, her açılışta yeni jeton (`bridge/run/bridge_token.txt`). MCP: `game_start`, `game_status`,
  `game_command`, `ingame_tour`, `game_stop` (bkz. `agent/AGENT.md`). Örnek görüntüler: `previews/ingame_fantasy_valley_256/`.

## Klasörler

| Klasör | İçerik |
|---|---|
| `terrain_engine/` | noise (Perlin/fBm/ridged/warp), generator (vadi, nehir, termal erozyon, yüzey, ağaç), hydraulic (global damla erozyonu), regions (bölge maskeleri), export+cache, WorldPainter köprüsü, dünya doğrulayıcı, CLI |
| `presets/` | mountain_valley, forest_hills, rocky_cliffs, river_valley, natural_plains, fantasy_forest_valley, west_forest_east_rocky |
| `scripts/` | dünya kurulum, Axiom mod seti aç/kapa, MC sunucu yükleme testi |
| `axiom/` | Lua Script Brush presetleri + `instance/mods` (Fabric API + Axiom 6.1.5) |
| `worldpainter/`, `heightmaps/`, `previews/` | ilk test haritasının kopyaları |
| `tools/` | WorldPainter 2.27.1 (portable) ve indirilen jar'lar |
| `agent/` | AI ajanı için düşük token iş akışı; MCP sunucusu `agent/mcp_server.py` (`.mcp.json`, bkz. `agent/AGENT.md`); oyun sürücüsü `bridge_client.py` |
| `bridge/` | Fabric 1.21.4 köprü modu (Loom projesi; `run/` = geliştirme istemcisinin oyun klasörü) |
| `docs/` | WorldPainter/Axiom notları, Bridge/MCP tasarımı |

## Minecraft'ta açma

Test dünyası `%APPDATA%\.minecraft\saves\fantasy_valley_256` içine kopyalandı. TLauncher → "Fabric 1.21.4" veya vanilla 1.21.4 → Tek Oyunculu.
Axiom için: `scripts\axiom_mods_on.ps1` (mevcut mods klasörünü yeniden adlandırarak saklar), bitince `scripts\axiom_mods_off.ps1`.
