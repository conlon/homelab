#!/bin/bash -e



disk_path="/dev/disk2"



if [ ! -f raspbian.zip ] && [ ! -f *raspbian*.img ]; then
    curl -L https://downloads.raspberrypi.org/raspbian_lite_latest -o raspbian.zip
fi
if [ ! -f *raspbian*.img ]; then
    unzip raspbian.zip
fi

img_file=`ls *raspbian*.img`

echo "===================="
echo "VERIFY ${disk_path} IS THE DISK YOU WANT IMAGED!"
echo "WRITING IN 30 SECONDS..."
echo "===================="
echo -e "\n\n\n\n"
diskutil list
echo -e "\n\n\n\n"
echo "===================="
echo "VERIFY ${disk_path} IS THE DISK YOU WANT IMAGED!"
echo "WRITING IN 30 SECONDS..."
echo "===================="
echo ""
echo "About to write ${img_file} to ${disk_path}..."
sleep 30

diskutil unmountDisk ${disk_path}
sudo dd bs=1m if=${img_file} of=${disk_path} conv=sync

echo "enabling ssh..."
touch /Volumes/boot/ssh

echo "boot disk is ready for raspberry pi."