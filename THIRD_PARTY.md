# Third-party components

Lemu itself is MIT-licensed (see `LICENSE`). It orchestrates external software
and, in the case of the boot artifacts, redistributes a small amount of GPL
code. Nothing proprietary is shipped in this repository.

## Redistributed in this repository

### Android-x86 9.0-r2 kernel and initramfs

```
boot/androidx86_64/kernel
boot/androidx86_64/initrd.img
```

Taken from the **Android-x86 9.0-r2 64-bit ISO**. The Linux kernel is
GPL-2.0-only; the initramfs is built from Android-x86 components under the
same licence. They are included because Lemu boots the guest with QEMU's
external `-kernel` / `-initrd`, so the emulator will not start without them.

- Project: <https://www.android-x86.org/>
- Source: <https://sourceforge.net/projects/android-x86/files/Release%209.0/>

If you redistribute Lemu, keep this attribution and make the corresponding
source available as the GPL requires.

## Downloaded or built by the user (not in this repository)

| Component | Licence | Role |
|---|---|---|
| Android-x86 9.0-r2 ISO / system image | GPL-2.0 / Apache-2.0 (mixed) | the guest operating system |
| QEMU | GPL-2.0 | virtual machine monitor |
| Android SDK Platform Tools (`adb`) | Apache-2.0 | device communication |
| Intel Houdini (`libhoudini.so`, `libnb`) | proprietary, Intel | ARM translation inside the guest |
| MEmu Play | proprietary | the original system this project reconstructs |

`images/`, `instances/`, `*.qcow2`, `*.iso` and the extracted Windows MEmu
payload are deliberately excluded by `.gitignore`. You create the master image
yourself by following *Building the Android-x86 master image* in `README.md`;
Houdini must be obtained from a licensed MEmu installation.

## Trademarks

"MEmu", "NoxPlayer", "GameLoop", "Android" and "Intel" are trademarks of their
respective owners. Lemu is an independent, unaffiliated reconstruction and is
not endorsed by any of them.
