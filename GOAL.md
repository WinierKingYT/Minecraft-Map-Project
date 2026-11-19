# GOAL — ana plan

## Hedef
Minecraft Java için yapay zekâ destekli harita üretimi: düz dünya tabanı → doğal arazi (kıtalar, dağlar, nehirler,
göller, mağaralar, biyomlar) → ileride yapılar. Hedef haritalar çoğunlukla 1000²–5000² lobi/MMO haritaları.
AI yalnızca plan/parametre üretir; blok hesabı yerel motorda. Hangi model kullanılırsa kullanılsın kalite aynı kalmalı.

## Şu an (v0.11.0, 2026-11-19)
Çalışan: parametrik motor + bölgeler, hidrolik erozyon, mağara/biyom, dere/göl, bitki detayları, edit-world (kopya),
4096² parçalı üretim, WorldPainter headless export + doğrulama, yerel MC 1.21.4 sunucu testi, MCP sunucusu,
doğal dil planlayıcı, Fabric köprü istemcisi (AI oyunu açıp ekran görüntüsü alır). 155 test.
Ayrıntı: `docs/CHANGELOG.md`, `docs/INTEGRATIONS.md`, `README.md`, `agent/AGENT.md`.

## Sıradaki işler (bu sırayla)
| # | İş | Kabul |
|---|---|---|
| 1 | Faz 9b — su ağı düzeltmeleri (1024²+ düz dereler, boncuk göller, çiçek/çalı dengesi) | oyun içi önce/sonra görüntü |
| 2 | B — sabit test haritaları (metrik + görüntü kaydı, regresyon karşılaştırması) | her değişiklikte otomatik |
| 3 | A — `assess_map` MCP aracı (kalite karnesi) | B ile aynı ölçütler |
| 4 | C — `estimate_build` (süre/RAM/disk ön tahmini) | 5000² için tahmin ±%30 |
| 5 | Faz 13 — kıta ve iklim planlayıcı (3000²–5000²) | 5000² üretim + doğrulama |
| 6 | Faz 14 — görsel bölge editörü (tarayıcıda çiz → regions) | çizimden harita |
| 7 | Faz 10 — edit-world v2 (ağaç/biyom/su, mağara tavanı) | kopya dünyada diff testi |
| 8 | Faz 15 — preset galerisi | tek tıkla preset |
| 9 | Faz 16 — kendi haritam (yalnız KOPYA üzerinde) | orijinale yazma yok |
| 10 | Faz 17 — model katmanlı AI kılavuzu/skill (Opus/Fable serbest; DeepSeek/GPT adım adım + kontrol listesi) | aynı görevde A/B ölçütleri tutar |
| 11 | E — `.schem` dışa aktarma (bölge/build) | WorldEdit/Axiom'da yapıştırılır |
| 12 | F — otomatik görev kaydı (`logs/`) | her görevde kayıt |
| ⏸ | Faz 12 yapılar; Distant Horizons/8192²+ | beklemede |

Her işte: önce yedek/commit, alt ajan kullanılabilir, sonunda `pytest` + `generate --verify` +
`scripts/mc_server_load_test.py` (+ görsel işlerde köprüyle oyun içi tur), belgeleri ve bu dosyayı güncelle, commit+push.

## Başka bilgisayarda devam
1. `git clone https://github.com/WinierKingYT/Minecraft-Map-Project` (private).
2. Python 3.13: `python -m venv .venv` → `.venv\Scripts\python -m pip install -r requirements.txt mcp`.
3. WorldPainter 2.27.1 portable zip (worldpainter.net) → `tools/worldpainter/WorldPainter/`; `wpscript.vmoptions` ve
   `worldpainter.vmoptions` sonuna `-Duser.language=en`, `-Duser.country=US`, `-Xmx6g` ekle (Türkçe locale hatası).
4. Axiom 6.1.5 (MC 1.21.4) jar → `tools/downloads/axiom/` (yalnız Axiom kullanılacaksa).
5. Sunucu testi: Mojang 1.21.4 `server.jar` → `tests/mc_server/` (EULA'yı kullanıcı kabul etmeli).
6. Köprü: JDK 25 (Temurin) PATH'te; `bridge\gradlew.bat runClient` ilk çalıştırmada oyun dosyalarını indirir.
7. `.mcp.json` içindeki mutlak yolları yeni makineye göre düzelt.
8. Kontrol: `.venv\Scripts\python -m pytest` yeşil olmalı.
