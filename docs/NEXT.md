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

## Sıradaki (kullanıcının seçtiği sıra: 11 → 7 → 8 → 9 → 10; 12 sonra)
- `[x]` **Faz 11 — canlı istemci köprüsü:** Fabric geliştirme istemcisi (Loom `runClient`, proje içi ayrı oyun klasörü) +
  localhost köprü modu: dünya aç, kamera konumla, ekran görüntüsü al, komut çalıştır. Agent oyunu kendisi açıp kontrol eder.
  Sonuç: `bridge/` + `agent/bridge_client.py` + 5 MCP aracı; fantasy_valley_256 turu 6 gerçek oyun içi görüntü (~40 sn, açılış ~16 sn),
  `previews/ingame_fantasy_valley_256/`; kısa çimen, biyom renkleri ve mağara görüldü.
- `[ ]` **Faz 7 — oyun içi kontrol:** köprüyle otomatik ekran görüntüleri; çimen, biyom renkleri, mağaralar, kıyı, 4096² harita.
- `[ ]` **Faz 8 — doğal dil komutu:** "batıya orman ekle" → `regions` JSON → MCP `generate`/`edit_world`; tek komut/skill.
- `[ ]` **Faz 9 — görsel kalite:** nehir kolları ve göller, 256² dik yamaç p90'ı, özel ağaç objeleri, bitki `.layer` dosyaları.
- `[ ]` **Faz 10 — edit-world v2:** düzenlenen alana ağaç/biyom yerleştirme, mağara tavanı koruması.
- `[ ]` **Faz 12 — yapılar (sonra):** ev, kale, köy, dungeon. Şu an yalnızca arazi.

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
