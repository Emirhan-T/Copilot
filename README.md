# Viraj — bitirme projesi, aşama 1

İki nokta arasındaki OSM rota geometrisinden **yaklaşık viraj adayları** çıkaran
Python prototipi. Mobil istemcinin tüketeceği JSON/GeoJSON sözleşmesi hazırdır.
Mobil uygulama ve canlı GPS takibi sonraki aşamalardır. Test arayüzünün yerel
servis API'si bu projeye eklendi.

## Viraj Lab — etkileşimli test sayfası

Rota seçimi ve parametre deneyleri için yerel web arayüzü hazırdır. Mevcut
Python çekirdeğini kullanır; web ve CLI aynı ayarlarda aynı adayları döndürür.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[web,dev]"
.\.venv\Scripts\python.exe -m viraj serve --port 8766
```

Tarayıcıda [Viraj Lab](http://127.0.0.1:8766) adresini aç. Sonraki açılışlarda
`start-lab.cmd` dosyasını çift tıklayarak da çalıştırabilirsin. Sunucu bu
bilgisayarda `127.0.0.1` adresinde çalışır; internet yayını yapılmaz.

1. Konya–Ankara hazır rotasını kullan, **enlem, boylam** yaz veya yanındaki
   hedef simgesiyle haritadan A/B noktalarını seç. OSM `#map=.../enlem/boylam`
   bağlantısı da koordinat alanına yapıştırılabilir. İşaretçiler sürüklenebilir.
2. **Minimum toplam dönüş açısını** ayarla. Bu, viraj boyunca biriken yatay
   yön değişimidir; yokuş açısı değildir. İlk değer 0,5°.
3. **Rotayı analiz et** düğmesine bas. Haritadaki viraja veya listedeki satıra
   tıklayarak o bölgeyi yakınlaştır; eğrilik sekmesi seçilen bölgenin ayrıntısını gösterir.
4. Açı ya da başka bir ayar değişince yeniden analiz et. Önceki sonuç bu sırada
   açıkça işaretlenir. Aynı rotada parametre değişimi yeni rota isteği oluşturmaz.
5. Sonucun JSON/GeoJSON dosyasını veya tüm örneklerin CSV'sini indir.

**Diğer test ayarları:** birleştirme boşluğu, minimum uzunluk, tespit yumuşatması,
ters dönüş toleransı, en geniş tespit yarıçapı, örnekleme ve yarıçap yumuşatması.
Yön filtresi yalnızca sonuç listesini süzer; haritada tüm adaylar kalır.

Leaflet 1.9.4 dosyaları yerel paket içindedir. Yeni rota almak ve OSM harita
altlığını görmek için internet gerekir. Sunucu son 16 rotayı ve 8 analiz
sonucunu bellekte tutar; kalıcı OSRM önbelleği `cache/` altındadır.
CLI analizleri diske yazarken web testleri indirilmeyen sonuçları bellekte tutar.
Harita seçimi, aynı çekirdek sonuçları, parametre doğrulama, açı değişimiyle
rota isteğinin tekrarlanmaması ve CSV/grafik çıktısı test edilmiştir.

Web API: `POST /api/routes` → `POST /api/analyze`. Başlangıç/bitiş gövdesi
`{"start":{"lat":37.8714,"lon":32.4846},"end":{"lat":39.9334,"lon":32.8597}}`;
analiz gövdesi `{"route_id":"...","config":{"min_turn_deg":0.5}}`.
`GET /docs` yerel API sözleşmesini gösterir.

## Güncel demo: Konya → Ankara

- Yaklaşık şehir merkezi noktaları: `[32.4846, 37.8714]` → `[32.8597, 39.9334]`.
- Yerel metrik geometri uzunluğu: **270,83 km**. OSRM mesafesi **270,93 km**;
  projeksiyon ve sağlayıcının mesafe hesabı nedeniyle aynı olmak zorunda değiller.
- Güncel hassas tespit: 5 m örnekleme, R ≤ 50.000 m başlatma eşiği,
  toplam yön değişimi ≥ 0,5°, L ≥ 15 m ile **383 aday**.
- Eski 300 m / 5° ayarıyla çıkan 92 aday karşılaştırma için
  `outputs/konya-ankara-v1/` altında saklandı. Güncel haritanın sağ üstündeki
  katman kontrolünde eski mor kesikli çizgiler açılabilir.
- Bunlar doğrulanmış fiziksel viraj sayısı değildir. Kavşak dönüşleri de dahildir.
- Kaynakta 2.234 nokta; segment medyanı yaklaşık 68 m, %95 yüzdeliği 396 m.
  Yeniden örnekleme yeni yol ayrıntısı üretmez.
- Aynı yönlü kısa parçalar birleştirilir; aday uçları arası boşluk en fazla 100 m,
  aradaki ters yön değişimi en fazla 0,15° olabilir. Belirgin S dönüşü korunur.
- Bildirilen nokta `[32.73005, 38.10124]` yakınındaki geniş sol viraj artık
  yaklaşık **34,64–35,71 km** arasında tek adaydır. Nokta, modellenmiş aday
  sınırına yaklaşık 3 m ve rota çizgisine yaklaşık 9 m uzaktadır.
  Kaynaktaki 1,28 km segment nedeniyle başlangıç/bitiş yaklaşık modellenir.
- Aynı örnekleme/yumuşatma taramasında aday sayısı artık **380–383**
  (eski sürüm: 47–128). Bu, bağımsız doğruluk veya tüm virajların yakalandığı anlamına gelmez.

İlk çalıştırmadan sonra:

`outputs/` ve `cache/` Git deposuna dahil değildir; çalıştırma sırasında üretilir.
Eski v1 karşılaştırma dosyaları bu çalışma bilgisayarındaki deney kayıtlarıdır.
Yeni bir klonda `--compare-to` kullanmadan önce karşılaştırılacak GeoJSON dosyasını
ayrıca oluşturmak veya sağlamak gerekir.

- `outputs/konya-ankara/map.html`: harita, seçilebilir viraj listesi, eğrilik grafiği.
- `outputs/konya-ankara/analysis.json`: v1.1 analiz sözleşmesi.
- `outputs/konya-ankara/bends.geojson`: rota + her viraj için LineString.
- `outputs/konya-ankara/samples.csv`: ortak `s` ekseni ve iki eğrilik hesabı.
- `outputs/konya-ankara/route_input.geojson`: indirilen kaynak geometri.
- `outputs/konya-ankara/route_sensitivity.png`: gerçek rota hassasiyeti.
- `outputs/konya-ankara/detection_sensitivity.png`: tespit yumuşatması / birleştirme boşluğu etkisi.
- `outputs/validation/report.md`: yapay yol deneyleri ve açık doğrulama işleri.

## Kurulum ve çalıştırma (PowerShell)

Python 3.11+ gerekir; bu çalışma Windows / Python 3.14 ile doğrulandı.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,osm]"
.\.venv\Scripts\python.exe -m viraj demo
Start-Process .\outputs\konya-ankara\map.html
```

İlk OSRM isteği internete çıkar; sonraki aynı istekler `cache/` üzerinden okunur.
GeoJSON dosyasıyla hesaplama tamamen çevrimdışı çalışır. HTML haritasının altlığı
ve CDN kütüphaneleri internet gerektirir; PNG grafikleri çevrimdışı açılır.

```powershell
# Kayıtlı geometriyle yeniden analiz; yeni rota isteği yapmaz.
.\.venv\Scripts\python.exe -m viraj analyze outputs/konya-ankara/route_input.geojson --smoothing 65 --out outputs/konya-ankara-65

# Bildirilen eksik virajı işaretle ve önceki tespit katmanını ekle.
.\.venv\Scripts\python.exe -m viraj analyze outputs/konya-ankara/route_input.geojson --out outputs/konya-ankara --compare-to outputs/konya-ankara-v1/bends.geojson --check-point 32.73005 38.10124

# Her giriş ve GeoJSON için sıra: BOYLAM ENLEM.
.\.venv\Scripts\python.exe -m viraj route --start 32.4846 37.8714 --end 32.8597 39.9334 --out outputs/rota

# Yerel, kısa OSMnx denemesi: yönlü yol ağı üzerinde uzunluğu en az olan yol.
.\.venv\Scripts\python.exe -m viraj route --provider osmnx --start 32.4846 37.8714 --end 32.4910 37.8780 --buffer 1000 --out outputs/osmnx-konya

# Yapay yollar, iki yöntem ve parametre hassasiyeti.
.\.venv\Scripts\python.exe -m viraj validate
.\.venv\Scripts\python.exe -m viraj sensitivity outputs/konya-ankara/route_input.geojson --out outputs/konya-ankara
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

OSMnx büyük alan indirmesini önlemek için prototipte uçlar arası mesafe 30 km
ile sınırlıdır. İndirilen kutu dışında daha iyi bir yol olabilir. Uçlar düğümlere
oturtulur; bu ilk OSMnx adaptörü dönüş kısıtlarını ayrıca çözmez.
OSRM sonucu sağlayıcının önerdiği sürüş rotasıdır; geometrik en kısa yol diye
etiketlenmez. Uzun Konya–Ankara demosunda bu adaptör kullanılır.

Kendi OSRM sunucun için `route --osrm-url http://localhost:5000 ...` kullan.
Genel demo sunucusu bir üretim servisi değildir: en fazla 1 istek/saniye, ağır
kullanım yok. Bir kez indirip önbellekle; toplu deneyleri kayıtlı GeoJSON'da yap.
[Demo sunucusu koşulları](https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server),
[FOSSGIS koşulları](https://routing.openstreetmap.de/about.html).

## Hesaplama

1. Sağlayıcıdan tam rota geometrisi alınır. OSRM: `overview=full&geometries=geojson`.
2. `[boylam, enlem]`, `pyproj` ve `always_xy=True` ile yerel WGS84 UTM'ye çevrilir.
   Konya–Ankara için EPSG:32636 seçilir. Türkiye tek bir UTM diliminde değildir;
   dilim rota merkezinden seçilir. Fazla geniş rotalar bölünmek üzere reddedilir.
3. Ardışık aynı noktalar kaldırılır. Kaynak metrik polilin boyunca birikimli mesafe
   `s` hesaplanır. İki uç da korunarak eşit aralık kullanılır; gerçek adım en fazla
   5 m'dir ve `actual_spacing_m` alanında yazılır. Kısa bir son aralık bırakılmaz.
4. x/y koordinatlarına Savitzky–Golay yumuşatması uygulanır. Pencere tek sayılıdır;
   gerçek fiziksel genişliği `quality.effective_smoothing_span_m` alanındadır.
   `θ = unwrap(atan2(dy/ds, dx/ds))`; `κ = (dθ/ds) / ||dr/ds||`.
   Son bölme, yumuşatılmış yolun yay uzunluğundaki değişimi düzeltir.
   İşaret: **pozitif sol**, **negatif sağ**, rotanın gidiş yönüne göre.
5. Aynı yumuşatılmış yol üzerinde üç noktalı çember eğriliği de hesaplanır:
   `κ₃ = 2 cross(B−A, C−B) / (|B−A| |C−B| |C−A|)`.
   İki yöntem ortak geçerli iç örneklerde karşılaştırılır. Uç pencere bölgeleri
   sınıflandırılmaz; çok kısa bir rota yeterli destek sağlamayabilir.
6. **Tespit sinyali yarıçap hesabından ayrıdır.** Kaynak segmentlerin orta
   bölgelerinde 20 m'lik kirişlerden yön hesaplanır. Bu yönler arasında doğrusal
   geçiş kullanılır; uzun segmentlerin uçlarında geçiş en fazla 150 m'ye yayılır.
   Önce hücre ortalaması alınarak seyrek örneklemede aliasing önlenir, sonra
   yön sinyaline Gaussian σ=20 m ve türev uygulanır. Böylece SG filtresinin
   küçük ters lobları gerçek S virajı gibi kullanılmaz. Bu geometriye dayanan
   bir sınır modelidir, kayıp yol ayrıntısının bağımsız ölçümü değildir.
7. `|κ_tespit| ≥ 1/50000` başlatma eşiğidir; devam eşiği bunun %25'idir.
   Aynı yönlü zayıf omuzlar adayda kalır. Aynı yönlü adayların uçları arasında
   en fazla 100 m varsa, boşluğun ters yöndeki **toplam açısı** en fazla 0,15°
   ise ve geçersiz örnek yoksa birleştirilir. S dönüşleri korunur.
   15 m'den kısa veya net açısı 0,5°'den küçük adaylar elenir.
   Bunlar başlangıç eşikleridir: matematiksel olarak her sıfır dışı eğriliği
   yakalamak, koordinat gürültüsünü de viraj sayar. Kaynakta bulunmayan kıvrım
   bu yöntemle kesin olarak geri getirilemez. Yatay viraj hesabı, yokuş eğimi değildir.
8. `R_min = 1/max(|κ|)` aynı yönlü aktif örneklerde hesaplanır.
   `R_p50`, tespit sinyali tepesinin en az %25'i kadar güçlü olan çekirdekteki
   aynı yönlü örneklerin medyan eğriliğinin tersidir. Düz birleştirme boşlukları
   yarıçap özetine katılmaz. `detection_min_radius_m` tespit sinyalinin minimum
   eşdeğer yarıçapını ayrıca verir; SG minimum yarıçapıyla eşdeğer değildir.
   `length_m = end_s_m − start_s_m`, yani **kaynak rota** eksenindeki uzunluktur.
   Aday içindeki kaynak nokta sayısı ve en uzun kaynak segmenti ayrıca yazılır.
   Üçten az kaynak noktası varsa `geometry_support=sparse`: bu yalnızca veri
   desteği işaretidir, kalibre edilmiş bir güven olasılığı değildir.

Minimum yarıçap tek bir gürültülü örneğe duyarlıdır. Medyan yarıçap hatasının
küçük çıkması minimum yarıçapın aynı doğrulukta olduğu anlamına gelmez.
İlk deneyde σ=0,15 m gürültülü yaylarda en büyük medyan yarıçap hatası %1,89
iken, en büyük minimum yarıçap hatası %48,59 oldu. Bu sınırlama raporda korunur.
Üç nokta yöntemi de aynı geometriyi kullandığı için bağımsız gerçek yol
doğrulaması yerine geçmez. `--radius-threshold`, `--min-length`, `--min-turn`,
`--merge-gap`, `--spacing`, `--smoothing`, `--detection-sigma`, `--release-ratio`,
`--heading-transition-cap` ve `--opposite-tolerance` deney parametreleridir.
Ters dönüş toleransı ayrıca minimum toplam dönüş açısının yarısıyla sınırlanır;
bu, hassasiyet artırıldığında gerçek karşı yön adayının yutulmasını önler.
Birleştirme boşluğu, düşük eşikle genişletilmiş aday uçları arasındaki mesafedir;
ham yoldaki düzleşme uzunluğundan farklı olabilir. Minimum toplam açıyı örneğin
`--min-turn 0.25` ile daha da düşürebilirsin; yanlış tespit deneylerini yeniden çalıştır.

## Çıktı sözleşmesi ve mobil bağlantı

[schemas/analysis.schema.json](schemas/analysis.schema.json) v1.1 şemasını tanımlar;
eski sözleşme `schemas/analysis-v1.0.schema.json` altında saklıdır.
Her aday `start_s_m`, `end_s_m`, `direction`, `min_radius_m`, `p50_radius_m`,
`length_m`, `turn_angle_deg`, tepe noktasının `apex_s_m` / `apex_lonlat` konumu,
uç koordinatları ve kaynak geometri desteğini taşır. Tüm mesafeler metre,
eğrilik 1/metre, açılar derecedir. GeoJSON koordinatları WGS84'tür.
`merged_part_count` birleştirilen tespit parçalarının sayısını verir. Parçalar
zayıf devam eşiğiyle zaten bağlıysa bu sayı 1 olabilir.

Python çekirdeği rota sağlayıcısından bağımsızdır:

```python
from viraj import Config, analyze_lonlat

analysis = analyze_lonlat(lonlat_coordinates, Config())
payload = analysis.summary()
```

Mobil geliştirmede ilk hedef: iki nokta seçimi → bu analiz sözleşmesini döndüren
servis → haritada rota/adaylar → liste ve ayrıntı. Mobil teknoloji seçimi henüz
yapılmadı. Canlı GPS eşleme ve yaklaşan viraj bildirimi ayrı aşamadır.

## Doğrulama durumu

- Otomatik testler: bilinen sol/sağ yaylar, gürültü, düz+yay birleşimi, S virajı,
  rotayı ters çevirme, UTM/koordinat sırası, uçlar, yönlü OSMnx kenar geometrisi,
  önbellek/ağ hataları ve JSON sözleşmesi.
- Deney raporu: 60 yay, 15 düz yol ve sabit girdi üzerinde 12 parametre çifti.
  Düz yolda yanlış viraj oranı = tespit edilen viraj uzunluğu / geçerli yol uzunluğu.
- R=500–30.000 m hafif yaylar, gürültülü düz yol, kısa düzleşmeyle birleştirme,
  uzun düz yolla ayırma, hafif S, seyrek kaynak geometrisi ve bildirilen gerçek
  konum için regresyon testleri eklendi. Bildirilen konumun kaynak kesiti
  `tests/fixtures/konya-reported-bend.geojson` ile çevrimdışı sınanır.
- Gerçek rota verisi ve hassasiyet grafiği üretildi.
- **Gerçek fiziksel yarıçap doğrulaması henüz tamamlanmadı.**
  [Ölçüm tablosu](docs/real_measurements.csv) ve
  [ölçüm yöntemi](docs/real-validation.md) hazır. Aşamanın bilimsel olarak
  tamamlandığını söylemek için bağımsız uydu/arazi ölçümleri gerekli.

## Kaynaklar

- [OSMnx referansı](https://osmnx.readthedocs.io/en/stable/user-reference.html)
- [OSRM Route API](https://project-osrm.org/docs/v5.24.0/api/)
- [pyproj dönüşümleri ve eksen sırası](https://pyproj4.github.io/pyproj/stable/examples.html)
- [SciPy Savitzky–Golay](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.savgol_filter.html)
- [SciPy Gaussian yön filtresi](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.gaussian_filter1d.html)
- [OpenStreetMap atfı ve lisansı](https://www.openstreetmap.org/copyright)

Rota geometrisi: © OpenStreetMap contributors. Kendi kodunun lisansı ayrıca seçilebilir.
