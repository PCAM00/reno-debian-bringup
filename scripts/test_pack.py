#!/usr/bin/env python3
import gzip
import struct
import unittest

from pack_boot import PAGE, pack


class HeaderTests(unittest.TestCase):
    def test_roundtrip_and_offsets(self):
        image=bytearray(256)
        image[56:60]=b'ARM\x64'
        dtb=struct.pack('>II',0xd00dfeed,8)
        rd=gzip.compress(b'070701' + b'test',mtime=0)
        boot,kernel=pack(bytes(image),dtb,rd)
        self.assertEqual(boot[:8],b'ANDROID!')
        fields=struct.unpack_from('<10I',boot,8)
        self.assertEqual(fields,(len(kernel),0x8000,len(rd),0x1000000,0,0,0x100,PAGE,1,0))
        self.assertEqual(len(boot)%PAGE,0)
        self.assertEqual(boot[PAGE:PAGE+len(kernel)],kernel)
        self.assertEqual(kernel[9],255)
        rd_start=PAGE+((len(kernel)+PAGE-1)//PAGE)*PAGE
        self.assertEqual(boot[rd_start:rd_start+len(rd)],rd)
        self.assertEqual(struct.unpack_from('<IQI',boot,1632),(0,0,1648))
        self.assertEqual(len(boot[:PAGE]),4096)
    def test_reject_invalid_image(self):
        with self.assertRaises(ValueError):pack(b'bad',b'',b'')


if __name__=='__main__':unittest.main()
