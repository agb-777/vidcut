# VidCut

VidCut adalah alat pemotong video berbasis FFmpeg yang menggunakan metode *Smart Cut*. Metode ini memungkinkan pemotongan video tanpa melakukan encoding ulang pada seluruh bagian video. Hanya bagian awal dan akhir yang perlu di-encode ulang, sedangkan bagian lainnya disalin langsung untuk mempercepat proses pemotongan.

## Fitur

- Smart Cut untuk video H.264 dan HEVC.
- Codec lainnya diproses menggunakan libx264.
- Mendukung format output MP4, MKV, dan MOV.
- Multi Cut untuk memotong beberapa bagian video sekaligus.
- Menggabungkan hasil potongan tanpa encoding ulang.
- Pengaturan preset, CRF, dan bitrate audio.
- Mempertahankan informasi rotasi, format piksel, dan warna video sumber.
- Progress bar dengan informasi kecepatan dan estimasi waktu.
- Mendukung pembatalan proses menggunakan `Ctrl+C`.
- Pembersihan file sementara secara otomatis.

## Persyaratan

- Python 3.9 atau lebih baru.
- FFmpeg 5.1 atau lebih baru.
- `ffmpeg` dan `ffprobe`.
- Opsi `-display_rotation` diperlukan untuk menangani video dengan informasi rotasi pada build FFmpeg yang mendukungnya.

## Instalasi

Clone repositori:

```bash
git clone https://github.com/agb-777/vidcut.git
cd vidcut
```

Instal menggunakan pip:

```bash
pip install .
```

Verifikasi instalasi:

```bash
vidcut --help
```

## Penggunaan

Format dasar:

```bash
vidcut <input> [options]
```

VidCut dapat dijalankan langsung tanpa instalasi menggunakan:

```bash
python -m vidcut --help
```

## Contoh Perintah

Memotong video berdasarkan waktu:

```bash
vidcut input.mp4 -s 00:01:00 -e 00:02:30
```

Memotong video dari detik tertentu hingga selesai:

```bash
vidcut input.mp4 -s 90 -o clip.mkv
```

Mengatur kualitas encoding:

```bash
vidcut input.mp4 -s 00:01:00 -e 00:02:00 --preset slow --crf 18 --audio-bitrate 256k
```

Memotong beberapa bagian video:

```bash
vidcut input.mp4 --clips clips.txt
```

Memotong dan menggabungkan hasilnya:

```bash
vidcut input.mp4 --clips clips.txt --merge
```

Format file `clips.txt`:

```text
# START END
00:00:00 00:01:00
00:02:00 00:03:30
00:10:15 00:10:45
```

## Opsi yang Tersedia

| Opsi | Keterangan |
|:--|:--|
| `-s, --start` | Waktu mulai pemotongan. Default: `0`. |
| `-e, --end` | Waktu akhir pemotongan. Default: akhir video. |
| `-o, --output` | Menentukan file atau direktori output. |
| `-c, --clips FILE` | Menggunakan daftar potongan dari file teks. |
| `-m, --merge` | Menggabungkan hasil Multi Cut. |
| `--preset` | Mengatur preset encoding. |
| `--crf` | Mengatur kualitas encoding. |
| `--audio-bitrate` | Mengatur bitrate audio AAC. |
| `--ffmpeg-dir DIR` | Menentukan direktori FFmpeg dan FFprobe. |
| `-y, --yes` | Menimpa file tanpa konfirmasi. |
| `-q, --quiet` | Menonaktifkan informasi progres. |

Format waktu yang didukung: `HH:MM:SS`, `MM:SS`, atau detik. Angka desimal juga diperbolehkan.

## Catatan

- Durasi hasil pemotongan dapat sedikit berbeda karena penyesuaian frame dan keyframe.
- Audio selalu di-encode ulang menggunakan AAC.
- Video Open-GOP dan interlaced akan di-encode ulang secara keseluruhan.
- File MPEG-TS (`.ts`, `.mts`, `.m2ts`) akan di-remux ke MKV sementara sebelum diproses. Proses ini membutuhkan ruang penyimpanan tambahan.
- File sementara akan dibersihkan setelah proses selesai atau dibatalkan.
- Pada Multi Cut, hasil potongan yang sudah selesai tetap disimpan jika proses gagal atau dibatalkan.
- Kode keluar: `0` berhasil, `1` proses gagal, `2` argumen atau file tidak valid, `130` dibatalkan.

## Lisensi

MIT License.
