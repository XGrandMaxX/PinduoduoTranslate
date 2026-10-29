"""Semantic selection/layout tests. Only synthetic product data is used."""
import unittest
from selection import select_targets
from mirror import Mirror, KEYFRAME
from translation import valid_translation


def node(text,box,name='pdd',cls='android.widget.TextView',parent=-1,clickable=False):
    return {'text':text,'box':box,'id':'com.xunmeng.pinduoduo:id/'+name,
            'class':cls,'parent':parent,'clickable':clickable}


class SelectionTests(unittest.TestCase):
    def test_material_and_number_changes_are_rejected(self):
        self.assertFalse(valid_translation('Cotton shirt','Шелковая рубашка'))
        self.assertFalse(valid_translation('Bag 2026','Сумка 2025'))
        self.assertTrue(valid_translation('Cotton shirt 2026','Хлопковая рубашка 2026'))

    def test_titles_and_actions_only(self):
        nodes=[node('',[0,100,360,750],cls='android.view.ViewGroup',clickable=True),
               node('Реклама',[10,180,340,230],parent=0),
               node('免费配送',[20,240,100,300],parent=0),
               node('Price: 39.90',[10,650,340,690],parent=0),
               node("Women's Handbag 2026",[16,600,344,640],'tv_title',parent=0),
               node('Store sales: 79K',[10,700,340,740],parent=0),
               node('Place order',[380,1440,700,1490],cls='android.widget.Button',clickable=True),
               node('Home',[10,1000,340,1040]),
               node('输入密码',[20,1100,340,1140],'tv_title'),
               node('',[0,150,360,590],cls='android.widget.ImageView',parent=0)]
        selected=select_targets({'nodes':nodes},720,1560)
        self.assertEqual([x['role'] for x in selected],['title','button'])
        self.assertEqual(selected[0]['pixels'],(16,600,328,40))
        self.assertEqual(selected[1]['translation'],'Заказать')
        self.assertEqual(selected[0]['lines'],1)

    def test_caption_does_not_enter_image_or_neighbor_card(self):
        boxes=[[16,600,344,640],[376,600,704,640]]
        selected=select_targets({'nodes':[node('Test product',box,'fixture-product-title') for box in boxes]},720,1560)
        self.assertEqual(len(selected),2)
        left,right=selected
        self.assertLessEqual(left['box'][0]+left['box'][2],right['box'][0])
        for item,original in zip(selected,boxes):
            self.assertEqual(item['pixels'],(original[0],original[1],original[2]-original[0],original[3]-original[1]))

    def test_nav_translation_stays_inside_its_own_button(self):
        nodes=[node('',[0,1414,144,1512],cls='android.widget.RelativeLayout',clickable=True),
               node('Home',[45,1480,99,1504],parent=0)]
        item=select_targets({'nodes':nodes},720,1560)[0]
        self.assertEqual(item['translation'],'Главная')
        self.assertEqual(item['pixels'],(4,1480,136,24))

    def test_images_and_clipped_nodes_are_not_titles(self):
        nodes=[node('Test',[0,-5,300,30],'tv_title'),
               node('Buy now',[0,200,300,500],cls='android.widget.ImageView',clickable=True)]
        self.assertEqual(select_targets({'nodes':nodes},720,1560),[])

    def test_exact_sold_out_notice_is_translated_in_its_native_box(self):
        nodes=[node('',[0,0,480,1009],cls='com.pdd.GoodsDetailSmartProductListView'),
               node('商品已售罄，推荐以下相似商品',[100,209,380,237],'tv_content',parent=0),
               node('Account private data',[10,40,300,80],'tv_content',parent=0)]
        items=select_targets({'nodes':nodes},480,1040)
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['role'],'notice')
        self.assertEqual(items[0]['translation'],'Распродано. Похожие ниже')
        self.assertEqual(items[0]['pixels'],(100,209,280,28))
        self.assertEqual(items[0]['scroll_box'],[0,0,480,1009])

    def test_slow_snapshot_decoder_does_not_create_an_unbounded_queue(self):
        mirror=Mirror();mirror.decode_config=b'synthetic-config'
        mirror._offer_decode(KEYFRAME,b'key')
        for _ in range(40):mirror._offer_decode(0,b'delta')
        self.assertLessEqual(mirror.decode_queue.qsize(),8)
        self.assertGreater(mirror.decode_dropped,0)
        self.assertTrue(mirror.decode_needs_key)
        mirror._offer_decode(KEYFRAME,b'current-key')
        self.assertFalse(mirror.decode_needs_key)


if __name__=='__main__':unittest.main()
