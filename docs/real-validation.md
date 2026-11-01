# Bağımsız gerçek yol doğrulaması

Durum: **bekliyor**. OSM geometrisinden tekrar çember uydurmak bağımsız doğrulama
sayılmaz. Başka kaynaktan uydu/hava görüntüsü veya ölçülmüş yol geometrisi gerekir.

1. Konya–Ankara haritasından 3–5 aday seç. Şehirler arası belirgin viraj,
   şehir içi kavşak dönüşü ve düz yol kontrolü ayrı örnekler olsun.
   Seçilen adayın ID'sini, koordinatını ve analiz ayarlarını kaydet.
2. Görüntünün kaynağını, görüntü tarihini, ölçüm tarihini ve metrik ölçeğini yaz.
   Yerel metrik CRS'de veya doğru ölçekli bir GIS görüntüsünde çalış. Eğik,
   perspektifli bir ekran görüntüsünden piksel yarıçapını doğrudan metre sayma.
3. Yol merkez çizgisinde yaklaşık dairesel bir yay seç. Aynı yöndeki taşıt yolunu
   ölç; karşı şerit veya yol dış kenarı farklı yarıçap verebilir.
4. Yayın iki ucunun kiriş mesafesini `c`, kiriş orta noktasından yaya dik en büyük
   uzaklığı `h` ölç. `R = c²/(8h) + h/2`. Küçük h değerinde ölçüm hatası büyür.
   Alternatif: birkaç bağımsız merkez çizgisi noktasına çember oturt.
5. Aynı yay bölgesinin algoritma yarıçapıyla karşılaştır. Tüm viraja oturtulan
   ortalama çemberi doğrudan tek örnekteki `min_radius_m` ile eşleştirme.
   En keskin bölgeyi ayrıca ölç veya bölge medyanlarını kullan.
6. `real_measurements.csv` içinde R_ölçüm, R_algoritma ve
   `100 × |R_algoritma − R_ölçüm| / R_ölçüm` hatasını yaz. Tekrar ölçümlerle
   kabaca ölçüm belirsizliği aralığı ver. Ham görüntüde işaretlenen yayı sakla.
7. Fark büyükse kaynak nokta sıklığını, kavşak olup olmadığını, şerit farkını
   ve yumuşatma etkisini incele. Sadece eşiği değiştirerek hatayı gizleme.

Viraj sınırları ile minimum yarıçapın doğrulanması ayrı sorulardır. Başlangıç ve
bitiş `s` değerleri, seçilen eğrilik eşiğine ve yumuşatmaya göre değişir.
