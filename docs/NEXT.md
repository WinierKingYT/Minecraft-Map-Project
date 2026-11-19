# NEXT — geliştirme yol haritası

Durum: `[ ]` bekliyor · `[~]` sürüyor · `[x]` bitti (doğrulandı) · `[!]` engelli
Kural: her faz sonunda `pytest` tamamı yeşil + `generate --verify` + `scripts/mc_server_load_test.py` geçmeli;
belgeler (README, docs/INTEGRATIONS.md, agent/AGENT.md, docs/CHANGELOG.md, bu dosya) güncellenmeli.

## Faz 0 — temel (v0.2.0) `[x]`
Parametrik motor, 6 preset, cache, parçalı üretim, WorldPainter headless export, NBT doğrulama,
MC 1.21.4 sunucu yükleme testi, kenar sönümü + harita dışı düz okyanus, Axiom ayrı mod seti + 4 Lua preset.

## Faz 1 — bölgesel düzenleme `[x]`
- Sonuç: `regions` + `terrain_engine/regions.py`; parçalı üretim bit-bit aynı, dikiş testi (sert kesim kontrolüyle), verify %100, sunucu 17/17.
"Batıya orman, doğuya kayalık" gibi istekler.
- `regions` listesi: şekil (yarım / kutu / çember / çokgen), `blend` (yumuşak geçiş, blok), preset ve/veya parametre farkları.
- Sayısal parametreler hücre hücre harmanlanır; seçim parametreleri (biome) baskın ağırlıkla.
- Kabul: parçalı üretim hâlâ bit-bit aynı; bölge sınırında dikiş yok (eğim testi); örnek preset `west_forest_east_rocky`.

## Faz 4 — agent için MCP aracı `[x]` (Faz 1 ile paralel; yalnız yeni dosyalar)
- Sonuç: 6 araç (`install_world` dahil), tek satır JSON, mcp 2.3.0 stdio; `tests/test_mcp.py` yeşil.
- `agent/mcp_server.py` (stdio): `list_presets`, `describe_params`, `generate`, `preview`, `server_load_test`.
- Kısa JSON yanıtlar (düşük token). Proje kökünde `.mcp.json`.
- Kabul: araç fonksiyonları için testler + stdio el sıkışma testi.

## Faz 2 — hidrolik erozyon `[x]`
- Sonuç: global damla geçişi (2 blok/hücre, önbellekli), parçalar bit-bit aynı; medyan eğim 41.1→30.6° (mountain_valley),
  36.2→26.6° (fantasy), rölyef %87/%94; 1024² 27 sn; verify %100, sunucu 17/17.
- Damla tabanlı erozyon: dereler, yarıklar, alüvyon; eğimi doğal yollarla düşürmek.
- Deterministik ve parçalı üretimle tutarlı (margin) olmalı.
- Kabul: dağlık presetlerde medyan eğim belirgin düşük; 1024² < ~30 sn; görsel önizleme karşılaştırması.

## Faz 3 — mağara, biyom, bitki çeşitliliği `[x]`
- Sonuç: `caves` (Caverns+Chasms) + `biomes` (Biomes katmanı); fantasy 256²: mağara havası %0.53 (kolonların %7.4'ü),
  nehir çekirdeği %98.8 minecraft:river, biyom eşleşmesi %92.4; verify %100, sunucu 17/17. Bitki katmanı atlandı (WP'de varsayılan yok).
- WorldPainter Caverns/Chasms katmanları; biyom haritası (nehir, orman, taiga, dağ); çiçek/çalı/eğrelti.
- Kabul: dünyada mağara boşlukları ve biyomlar NBT doğrulamasıyla görülür; sunucu testi geçer.

## Faz 5 — mevcut haritayı düzenleme `[x]`
- Sonuç: `edit-world` + MCP `edit_world`; kutu dışı tüm bloklar ve dokunulmayan chunk blob'ları birebir, kaynak hash aynı, kenar sıçraması +≤1, sunucu 16/16.
- Var olan dünyadan bölge oku → heightmap → motorla düzenle → kenarları harmanlayarak geri yaz.
- Yalnızca KOPYA dünyada çalışır; asla kullanıcının orijinal kaydına yazmaz.
- Kabul: kopya dünyada bölge değişir, bölge dışı bloklar birebir aynı kalır (diff testi).

## Faz 6 — büyük MMO haritaları `[x]`
- Sonuç: 4096² mountain_valley `--tile 1024`: üretim 280–320 sn (tepe RAM ~2.9 GB: ana + 8 işçi), WP export ~3 dk (`-Xmx3g` yetiyor),
  dünya 496 MB / 65 536 chunk, verify 9/64 bölge %100 kapsama + %100 ±1 yükseklik, sunucu 52/52; hidrolik ölçek-duyarlı
  (arazi eğimi medyan/p90, kapalı → eski → yeni: 1024² 15.0/33.1 → 20.4/50.2 → 12.8/28.1; 4096² 10.4/27.9 → 8.8/42.4 → 8.0/20.5; 256² değişmedi 30.6/59.4).
- 4096²+ : parça parça üretim + WorldPainter bölge bölge export, önizleme mozaiği, bellek limitleri.
- Kabul: 4096² harita üretilir, export edilir, sunucuda örnek noktalar doğrulanır; süre ve bellek raporlanır.
- Çözüldü (Faz 2'den): `hydraulic` 1024²'de eğimi artırıyordu → kaba hücre harita boyutuyla büyüyor, şev açısı hücreyle azalıyor;
  hidrolik geçiş artık tam haritayı tam çözünürlükte bellekte tutmuyor (parçalı ön-yükseklik, yalnız kaba ızgara).

## Sıradaki — kullanıcının onayladığı sıra (2026-11-19)
1. `[ ]` **Faz 9b — su ağı düzeltmeleri:** 1024²+ haritalarda dereler fazla düz / dik açılı; göller derelere boncuk gibi
   diziliyor; çiçek/çalı oranı dengesiz. Referans: `previews/phase9/mountain_valley_1024_overview_after.png`.
2. `[ ]` **Faz 13 — kıta ve iklim planlayıcı:** kaba ölçekte kıta/okyanus, dağ zincirleri, nehir havzaları, sıcaklık+nem →
   biyomlar; motor detayı bunun üzerine ekler. Büyük MMO haritasının asıl eksiği.
3. `[ ]` **Faz 14 — görsel bölge editörü:** tarayıcıda harita önizlemesi üzerine fırça/çokgenle bölge çiz + preset seç →
   `regions` JSON → üret. Koordinatı kimse elle yazmaz.
4. `[ ]` **Faz 10 — edit-world v2:** düzenlenen alana ağaç/biyom/göl/dere/bitki, mağara tavanı koruması.
5. `[ ]` **Faz 15 — preset galerisi:** üretilen haritaları seed+parametreyle yan yana önizle, beğenileni tek tıkla preset yap.
6. `[ ]` **Faz 16 — kendi haritam:** kullanıcının gerçek haritasının KOPYASI üzerinde edit-world ile iyileştirme akışı.
7. `[ ]` **Faz 17 — AI kullanım kılavuzu (model katmanlı skill):**
   - Güçlü modeller (Opus, Fable): hedef + kısıtlar + araç listesi; nasıl yapacaklarına kendileri karar verir.
   - Daha zayıf/ucuz modeller (DeepSeek, GPT vb.): adım adım tarif, kesin JSON şemaları, örnek girdi/çıktı, her adımda
     kontrol listesi ve kabul ölçütü; serbest karar yerine `plan_terrain` + doğrulama araçları.
   - Ortak: aynı MCP araçları; güvenlik ve doğrulama araçların içinde (zayıf model hatası dünyayı bozamaz);
     kaliteyi model değil ölçütler belirler (aşağıdaki öneri A/B ile birlikte).
   - Hedef: hangi model kullanılırsa kullanılsın aynı veya daha iyi kalite.
- `[⏸]` **Faz 12 — yapılar:** ev, kale, köy, dungeon. Beklemede (henüz hazır değil).
- `[⏸]` Distant Horizons / 8192²+ uyumu: sonraya.

## Kullanım amacı (kullanıcıdan, 2026-11-19)
- Sıfırdan harita: düz dünya tabanı → üstüne arazi → arazinin içine evler/binalar/yapılar → iyileştirme.
- Boyutlar çoğunlukla büyük: ~1000², 3000², 5000² (örnek; daha küçük de olabilir). Lobi veya MMO haritası:
  kıtalar ve kıtalara uygun mekânlar. Seed seçimi kullanıcı için önemsiz (determinizm için motor içinde kalır).
- Sonuç: Faz 13 (kıta/iklim) ve yapı yerleşimi için düz/hazır alanlar (Faz 12 hazırlığı) bu amaca göre tasarlanmalı;
  3000²/5000² gibi 16'nın katı olmayan boyutlar en yakın 16 katına yuvarlanır.

## Öneriler (2026-11-19 kararları)
- `[✓ eklendi]` **A. `assess_map` MCP aracı:** nesnel kalite karnesi (eğim, kaya/orman/su oranı, dikiş/sel kontrolü,
  isteğe bağlı oyun içi görüntü) → her model kaliteyi aynı ölçütle değerlendirir. Faz 17'nin temeli.
- `[✓ eklendi]` **E. Şematik dışa aktarma:** haritadan seçilen bölgeyi veya bir build'i `.schem` (WorldEdit/Axiom) olarak
  dışa aktarma; başka dünyalara yapıştırma.
- `[✓ eklendi]` **F. Otomatik görev kaydı:** her görevde süre, araç çağrıları ve (sağlayıcı verirse) token sayısı `logs/`.
- `[✓ eklendi]` **B. Altın regresyon seti:** sabit örnek haritaların metrik+görüntüleri kayıtlı; her kod
  değişikliğinde karşılaştırılır → kalite geri gitmez.
- `[✓ eklendi]` **C. `estimate_build`:** büyük haritayı üretmeden önce "ne kadar sürer, ne kadar RAM/disk ister"
  tahmini (ör. 5000² için dakika ve GB) — yanlışlıkla saatlerce sürecek bir iş başlatmayı önler.
- `[✗ reddedildi]` **D. Seed tarayıcı:** kullanım amacına uymuyor (seed seçimi önemsiz).

## Günlük
- 2026-11-19: Faz 0 tamam. Fazlar alt ajanlarla sırayla yürütülecek (1+4 paralel, sonra 2, 3, 5, 6).
- Her faz öncesi kod yedeği: `backups/<sürüm>-code.tgz` (araçlar, venv, exportlar hariç).
- 2026-11-19: Faz 4 tamam — `agent/mcp_server.py` + `.mcp.json`; testler yeşil.
- 2026-11-19: Faz 1 tamam — `regions` (bölge maskeleri), `west_forest_east_rocky` preset; testler yeşil.
- 2026-11-19: Faz 2 tamam — `hydraulic` (v0.4.0); 1024²'de eğim düşmüyor (sırt/dere ağı belirginleşiyor), not edildi.
- 2026-11-19: Faz 3 tamam — `caves` + `biomes` (v0.5.0); bitki katmanı atlandı. Not: WP undergrowth'u `minecraft:grass`
  (1.20.3 öncesi ad) yazıyor; DataVersion 3837'de `short_grass` olmalı — oyunda kontrol edilmeli.
- 2026-11-19: Faz 3 doğrulandı (80 test, sunucu 17/17); `minecraft:grass` → `short_grass` export düzeltmesi eklendi.
- 2026-11-19: Faz 5 tamam — `terrain_engine/edit_world.py` (v0.6.0, motor çıktısı değişmedi); ağaç ekleme yok, ışık oyunca yeniden hesaplanıyor.
- 2026-11-19: Faz 5 doğrulandı (97 test; edit-world demo: iç hata 0, kenar basamağı ≤2, sunucu 32/32). Sunucu testindeki sessiz yoklama hatası düzeltildi (44/44).
- 2026-11-19: Faz 6 tamam (v0.7.0) — ölçek-duyarlı hidrolik, paralel parçalı üretim, akışlı verify/anvil_fix; 4096² uçtan uca (WP tek parça, bölmeye gerek yok).
- 2026-11-19: Faz 11 tamam (v0.8.0) — Loom dev istemcisi + localhost köprü modu; ajan oyunu kendisi açıp 6 ekran görüntüsü aldı ve kapattı.
- 2026-11-19: Faz 6 doğrulandı (104 test; 4096² verify %100, sunucu 52/52). MCP `generate` parçalı üretimi destekliyor.
- 2026-11-19: Faz 7 tamam (v0.9.0) — oyun içi önce/sonra: kaya karışımı, kıyı/deniz derinliği, "Not a string" (WP level.dat Player boyutu) düzeltildi; 127 test, verify %100, sunucu 52/52 ×3.
- 2026-11-19: Faz 8 tamam (v0.10.0) — doğal dil → plan JSON (kural tabanlı, TR/EN), plan geçmişi + revizyon, MCP/CLI/skill; uçtan uca verify %100, sunucu 52/52.
- 2026-11-19: Faz 9 tamam (v0.11.0) — dere ağı + göller (WP `setWaterLevelAt`), export sonrası doğal objeler, 256² p90 düştü; verify %100, sunucu 60/60 ×3, oyun içi önce/sonra.
- 2026-11-19: Yol haritası yeniden sıralandı (9b → 13 → 14 → 10 → 15 → 16 → 17; 12 beklemede); öneriler A–F onay bekliyor.
- 2026-11-19: Öneriler: A, E, F eklendi; D reddedildi; B, C açıklama bekliyor. Kullanım amacı kaydedildi.
- 2026-11-19: B ve C de eklendi. Ana plan: kökteki `GOAL.md`.
