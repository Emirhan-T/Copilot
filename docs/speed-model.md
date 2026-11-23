# Hedef hız ve uyarı mesafesi modeli (aşama 2)

Kod: `src/viraj/speed.py` · Testler: `tests/test_speed.py`

Bu katman, viraj tespitinin bulduğu adayları değiştirmez. Her adaya bir hedef
hız hesaplar. Yalnızca hedef hızı yaklaşma hızının belirgin altında kalan
adaylar **uyarı** alır. Böylece tespit hassas kalırken sürücüye giden uyarılar
fiziksel bir ölçüte dayanır, elle seçilmiş bir açı eşiğine dayanmaz.

## 1. Hedef hız

Nokta kütle viraj modeli [1]:

    v² = g · R · (e + f(v))

- `g = 9,81 m/s²`.
- `f(v)`: AASHTO 2018, Tablo 3-7'deki **en büyük yanal sürtünme katsayısı**
  (15 mph'de 0,32 → 80 mph'de 0,08) [1], [2]. Değerler km/s'e çevrilip doğrusal
  enterpole edilir; tablo dışında uç değerde sabitlenir.
  - Bu katsayılar sürücü konforuna göre seçilmiş tasarım sınırlarıdır.
    Lastiğin kayma anındaki tutunması bunların çok üstündedir. Kayma sınırı
    kullanılsaydı hesaplanan "güvenli" hız tehlikeli derecede yüksek çıkardı [1].
  - `f` hıza bağlı olduğu için denklem ikiye bölme (bisection) ile çözülür.
    `f` hız arttıkça azaldığından kök tektir.
- `e` (dever): OSM verisinde yok. Varsayılan `e = 0` temkinli seçimdir.
  Gerçek yolların çoğunda dever olduğundan bu seçim fazla uyarı üretebilir.
- `friction_scale`: hava durumu aşaması için ayrılmıştır. Kuru yolda 1,0'dır.

### Tavan yok: her virajın kendi üst sınırı
Hedef hız hiçbir hızla sınırlanmaz; her viraj kendi fiziksel üst sınırını
gösterir. 200 km/s üstü "200+" (`target_is_unbounded`) olarak raporlanır.
Bu tür virajlar bir otomobili pratikte sınırlamaz. Konya–Ankara'da 383
adayın 185'i bu durumda. **Referans hız** (varsayılan 90 km/s) artık yalnızca
listedeki "yavaşlama gerektirir" işaretini ve seri gruplamasını belirler.

## 2. Hız için kullanılan yarıçap

`min_radius_m` ve `p50_radius_m` hız hesabına uygun değildir:

| Konya–Ankara (≥10° 97 viraj), yumuşatma 25 m → 85 m | Yarıçap oranı (medyan) |
|---|---:|
| `p50_radius_m` | 2,16× |
| Çekirdek yön değişimi yarıçapı | 1,00× |
| Kaynak noktalara çember uydurma | 1,00× |

Yarıçap 2,16 kat değişirse hız √2,16 ≈ 1,47 kat değişir. Bu yüzden:

1. **Çekirdek:** tespit sinyalinin tepe değerinin en az %70'ine ulaştığı bölge.
2. **Yön değişimi yarıçapı:** `R = L_çekirdek / θ_çekirdek`. Bu değer tespit
   sinyalinden gelir, SG yumuşatma ayarına bağlı değildir.
3. **Çember uydurma:** çekirdekte en az 4 kaynak OSM noktası varsa bu noktalara
   en küçük kareler çemberi uydurulur. Daha dar sonuç verirse o kullanılır
   (`min`, yani temkinli tarafta kalınır).
4. **Şerit payı:** küçük toplam dönüşlerde sürücü yolu şerit içinde
   düzleştirebilir. Toplam açısı θ olan bir dönüş, polilin köşesinden en fazla
   `w` uzaklaşan ve yarıçapı yaklaşık `8w/θ²` olan bir yolla geçilebilir.
   Varsayılan `w = 0,5 m`. Hız yarıçapı bu değerin altına inmez. Böylece OSM
   çizimindeki 1–2°'lik kırılmalar uyarı üretmez.

Güven etiketi kalibre edilmiş bir olasılık değildir, yalnızca veri desteğini
gösterir:

- `high`: çember uydurma var ve yön değişimi yarıçapıyla en fazla %25 farklı.
- `medium`: çember uydurma var ama fark büyük, ya da çekirdekte 3 kaynak noktası var.
- `low`: çekirdekte 3'ten az kaynak noktası var. Yarıçap enterpolasyon modelinden gelir.

### Yapay yaylarda doğrulama

Yay öncesi ve sonrası 400 m düz yol; kaynak noktalar 2 m aralıklı.

| R (m) | Açı | Gürültü σ (m) | p50 / R | Hız yarıçapı / R | Güven |
|---:|---:|---:|---:|---:|---|
| 30 | 30° | 0 | 2,22 | 1,20 | orta |
| 30 | 90° | 0 | 1,54 | 1,00 | orta |
| 60 | 30° | 0,5 | 2,66 | 0,65 | orta |
| 60 | 90° | 0 | 1,00 | 1,00 | yüksek |
| 120 | 30° | 0,5 | 1,43 | 0,64 | orta |
| 120 | 90° | 0,5 | 1,01 | 1,00 | yüksek |
| 250 | 30° | 0,5 | 1,10 | 0,91 | yüksek |
| 500 | 90° | 0,5 | 0,73 | 1,00 | yüksek |

Hız yarıçapı çoğu durumda doğru çıkıyor ya da gerçek yarıçaptan küçük kalıyor
(küçük yarıçap daha düşük hız demek, yani temkinli tarafta hata). Bilinen
zayıflık: kısa ve keskin yaylarda (R=30 m, 30°, yay boyu 16 m) yarıçap %20
büyük çıkıyor. Bu tablo gerçek yol doğrulaması yerine geçmez.

## 3. Hız profili, viraj serileri ve uyarı kuralı

### 3.1 Yavaşlama gerektiren viraj
Yaklaşma hızı `v₀` tek bir varsayılan değerdir: 90 km/s (KGM'ye göre otomobiller
için şehirlerarası çift yönlü yol sınırı [4]). `v₀ − v_hedef ≥ 8 km/s` olan
viraj **yavaşlama gerektirir** (`requires_slowdown`). MUTCD, hız sınırı ile
öneri hızı arasındaki fark 5 mph (≈ 8 km/s) olduğunda viraj uyarı levhası önerir [3].

### 3.2 İzin verilen hız profili
Rota 5 m aralıklarla örneklenir:

1. Her örneğe sınır yazılır: virajların çekirdeğinde hedef hız, başka yerde `v₀`.
2. Rota **sondan başa** taranır. Her noktada, sonraki tüm sınırlara `a = 3,4 m/s²`
   ile fren yaparak yetişilebilecek en yüksek hız hesaplanır:

       v(s) = min( sınır(s), √( v(s+ds)² + 2·a·ds ) )

Bu eğri, "bu noktadan en fazla kaç km/s ile geçilebilir" sorusunun cevabıdır.
Yakın virajlar bu eğride kendiliğinden birleşir.

### 3.3 Viraj serisi (yakın virajları tek uyarıda toplama)
Profil bu kez baştan sona taranır. Virajdan çıkan sürücünün varsayılan
`1,0 m/s²` ile hızlandığı kabul edilir. Bu varsayım yalnızca gruplama için
kullanılır ve kaynak gösterilmesi gereken bir değerdir. Bu ulaşılabilir hız
`v₀ − 8 km/s` altında kaldığı sürece içinde kalan tüm yavaşlama virajları
**tek seri** sayılır. Seri kaydında şunlar bulunur:

- `bend_ids`: serideki virajlar
- `critical_bend_id`, `min_target_kmh`: dizinin en düşük hedef hızı. Sürücüye
  ilk virajın değil, en keskin virajın hızı söylenir.
- `slowdown_start_s_m`: izin verilen hızın `v₀ − 3 km/s` altına ilk indiği nokta
- `trigger_s_at_approach_m`: `v₀` ile gelen sürücünün uyarılacağı nokta
  (`slowdown_start − v₀·t_tepki`)
- `message`: örneğin "4 viraj arka arkaya (230 m): en düşük 35 km/s"
- `envelope`: profilin bu seriye ait parçası, 10 m aralıklı `[s, km/s]` noktaları.
  Telefon profili yalnızca bu noktalardan yeniden kurar; seri dışında hız `v₀`'dır.
  Konya–Ankara'da yeniden kurulan profil, tam profilden en fazla 0,05 km/s yüksek
  (yuvarlama) ve en fazla 1 km/s düşük çıkıyor (temkinli tarafta).

### 3.4 Canlı uyarı kuralı: yalnızca hız aşılıyorsa (`OverspeedMonitor`)
Telefon her GPS güncellemesinde `(rota konumu s, hız v, zaman t)` verir:

1. Önündeki tepki mesafesine bakılır: `[s, s + v·t_tepki]` içindeki en düşük izinli hız.
2. **Uyarı yalnızca** `v > izinli + 3 km/s` ise verilir. 3 km/s, GPS hız gürültüsü
   için varsayılan toleranstır.
3. Bir seride ilk aşımda tek uyarı verilir: seri mesajı.
4. Aynı seride tekrar uyarı yalnızca iki durumda verilir (en az 4 s arayla):
   - `step`: sürücü uyarıya uymuş, sonra serinin daha düşük bir basamağında yine
     aşıyor. Örnek: "Daha da yavaşla: 35 km/s".
   - `reminder`: sürücü yavaşlamıyor. Örnek: "Hâlâ hızlısın: 35 km/s".
   Sürücü yavaşlıyorsa ikinci uyarı verilmez.

Ankara girişindeki 4 viraj (#351–354) için benzetim (yalnızca o kesim sürülür):

| Sürücü | Uyarılar |
|---|---|
| 30 km/s ile geliyor | yok |
| 90 km/s, hiç yavaşlamıyor | seri mesajı + 3 hatırlatma |
| İlk uyarıdan sonra profile uyuyor | seri mesajı + 1 basamak uyarısı (48 → 35) |
| Yalnızca ilk virajın hızına (48) iniyor | seri mesajı + basamak + hatırlatma |

Eski tasarımda aynı kesimde 200 m içinde 4 ayrı uyarı veriliyordu. #352'nin
uyarısı yavaşlamış sürücüye de gidiyordu.

### 3.5 Web sürüş simülasyonu
Web test sayfasında rota boyunca ilerleyen bir araç vardır:

- **Hız çubuğu:** 0–200 km/s, araç hızını anlık değiştirir.
- **Ok:** sonraki virajın yönüne ve sertliğine göre şekil alır. Toplam dönüş
  açısı arttıkça ok daha çok kıvrılır, güvenli hız düştükçe kıvrım yarıçapı
  daralır.
- **Viraja kalan mesafe:** virajın başlangıcına kadar olan mesafe; virajın
  içindeyken virajın sonuna kalan mesafe gösterilir.
- **Güvenli hız:** virajın fiziksel üst sınırı (tavan yok).
- **Durum:**
  - yeşil: hız güvenli hızın altında.
  - turuncu: hız virajı aşıyor ama frenlemeye yer var; gereken fren mesafesi gösterilir.
  - kırmızı "YAVAŞLA": §3.4'teki kural. Tepki mesafesinden sonra fren yapılsa
    bile önündeki virajların herhangi birinde güvenli hız aşılıyorsa çalar.
- Oka yalnızca güvenli hızı seçilen eşiğin altındaki virajlar girer
  (varsayılan 150 km/s). Uyarı hesabında ise 200 km/s altındaki tüm virajlar kullanılır.
- Sunucu aracın izlediği hattı 20 m aralıklı `track` (`s`, `lonlat`) olarak verir.

## 4. Konya–Ankara sonucu (383 aday)

Varsayılan ayarlar (90 km/s, e = 0):

- **39** viraj yavaşlama gerektiriyor, bunlar **22 uyarı serisinde** toplanıyor.
- 10 seri birden fazla viraj içeriyor; en büyüğü 5 viraj (Ankara girişi, 580 m).
- Seriler tetik noktasıyla birlikte web haritasında kırmızı halka olarak görünür.

Yavaşlama gerektiren viraj sayısının dever ve yaklaşma hızına göre değişimi:

| Yaklaşma hızı | e | Yavaşlama virajı | Şehirlerarası kesim* | Düşük güven |
|---:|---:|---:|---:|---:|
| 90 | 0 | 39 | 11 | 13 |
| 90 | 0,04 | 34 | 8 | 12 |
| 90 | 0,08 | 28 | 4 | 7 |
| 110 | 0 | 62 | 32 | 27 |
| 110 | 0,04 | 48 | 20 | 18 |
| 110 | 0,08 | 42 | 14 | 14 |

\* Rotanın ilk 5 km'si ve son 10 km'si hariç.

90 km/s ve e=0 ayarındaki 39 virajın 28'i rotanın şehir içi uçlarında. Bunlar
büyük ölçüde kavşak dönüşleridir. Şehir içinde yaklaşma hızı 50 km/s olmalı.

## 5. Bilinen sınırlar ve sıradaki işler

1. **Yola göre yaklaşma hızı:** OSM `maxspeed` / `highway` etiketleri ya da
   KGM'nin yol türüne göre varsayılan sınırları (50/90/110/130) okunmalı.
   OSRM'nin genel `annotations` parametresinde `maxspeed` alanı yok. Alternatif:
   `annotations=nodes` ile OSM düğüm kimliklerini alıp Overpass'tan etiketleri
   sorgulamak.
2. **Kavşak etiketi:** OSRM `steps=true` manevra noktalarıyla çakışan adaylar
   "kavşak dönüşü" olarak ayrılmalı.
3. **Hızlanma varsayımı:** seri gruplamasındaki 1,0 m/s² için kaynak eklenmeli
   ya da telefon verisinden ölçülmeli. Değer büyürse seriler bölünür, küçülürse birleşir.
4. **Hava durumu:** `friction_scale` alanı hazır. Islak, karlı ve buzlu yüzey
   katsayıları kaynaklı olarak bir sonraki aşamada eklenecek.
5. **Doğrulama:** hedef hızlar ölçülmüş araç hızlarıyla henüz karşılaştırılmadı.
   Telefonla `a_yanal = v·ω` ve `R = v/ω` ölçümü bu açığı kapatmak için planlandı.

## Kaynaklar (IEEE)

[1] AASHTO, *A Policy on Geometric Design of Highways and Streets*, 7th ed.
Washington, DC, USA: AASHTO, 2018.

[2] Iowa DOT, "Superelevation," *Design Manual*, ch. 2B, Table 2B.1 (AASHTO
2018 Table 3-7'den uyarlanmıştır). [Çevrimiçi]. Erişim:
https://ia.iowadot.gov/design/dmanual/2B.pdf

[3] FHWA, *Manual on Uniform Traffic Control Devices*, 2009 ed., Table 2C-5.
[Çevrimiçi]. Erişim:
https://highways.dot.gov/safety/local-rural/roadway-departure-safety-manual-local-rural-road-owners/appendix-b-2009-mutcd

[4] T.C. Karayolları Genel Müdürlüğü, "Hız sınırları." KGM araç sınıfı ve yol
türüne göre azami hız tablosu (otomobil: yerleşim içi 50, şehirlerarası
çift yönlü 90, bölünmüş yol 110, otoyol 130/140 km/s).

[5] D. B. Fambro, K. Fitzpatrick, and R. J. Koppa, *Determination of Stopping
Sight Distances*, NCHRP Report 400. Washington, DC, USA: TRB, 1997.
