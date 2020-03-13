## Building QEMU from source code
P<sup>2</sup>IM borrows the [build script](../qemu/build_scripts/) from [GNU MCU Eclipse QEMU](https://gnu-mcu-eclipse.github.io/qemu/). The script builds QEMU in a docker container.

The steps to compile QEMU is as follows.

### Install docker
Follow the steps in https://docs.docker.com/install/linux/docker-ce/ubuntu/ to install docker.

Then enable non-root user to run docker containers by
```bash
sudo usermod -aG docker $USER
```

Remember to log out and log back in for this to take effect!


### Install dependencies
```bash
sudo apt-get install curl git automake texinfo
```


### Build QEMU
```bash
cd qemu/ # cd to ROOT_OF_REPO/qemu/
WORK_FOLDER_PATH=`pwd`/src ./build_scripts/build-qemu.sh --deb64 --no-strip
```


You can found QEMU binary at `src/install/debian64/qemu/bin/qemu-system-gnuarmeclipse`
