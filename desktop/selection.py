"""Fail-closed selection: real product title IDs and a small action dictionary."""
import re

TITLE_IDS={'tv_title','goods_name','goods_title','tv_goods_name','tv_goods_title',
           'product_title','product_name','goods_name_text','fixture-product-title'}
BUTTONS={
    '加入购物车':'В корзину','已加入购物车':'В корзине','立即购买':'Купить',
    '马上购买':'Купить','单独购买':'Купить отдельно','发起拼单':'Заказать вместе',
    '去拼单':'Заказать вместе','去结算':'Оформить','结算':'Оформить',
    '提交订单':'Заказать','确认订单':'Подтвердить заказ','确认支付':'Оплатить',
    '立即支付':'Оплатить','去支付':'Оплатить','确定':'Готово','确认':'Подтвердить',
    '取消':'Отмена','返回':'Назад','下一步':'Далее','搜索':'Поиск',
    '购物车':'Корзина','首页':'Главная','我的':'Профиль',
    'buy now':'Купить','add to cart':'В корзину','added to cart':'В корзине',
    'checkout':'Оформить','check out':'Оформить','place order':'Заказать',
    'submit order':'Заказать','confirm order':'Подтвердить заказ','order now':'Заказать',
    'pay now':'Оплатить','confirm payment':'Оплатить','continue':'Далее',
    'next':'Далее','back':'Назад','cancel':'Отмена','confirm':'Подтвердить',
    'search':'Поиск','home':'Главная','cart':'Корзина','you':'Профиль',
    'following':'Подписки','bundles':'Совместно','sale':'Акции',
    'buy':'Купить','order':'Заказать','place an order':'Заказать','add to bag':'В корзину',
    'select':'Выбрать','choose':'Выбрать','done':'Готово','apply':'Применить',
    'select options':'Выбрать вариант','size':'Размер','color':'Цвет','quantity':'Количество',
    'agree':'Согласен','disagree':'Не согласен','view all':'Показать всё','see all':'Показать всё',
}
NO_TITLE=re.compile(r'验证码|密码|银行卡|收货地址|\b(?:password|verification|address)\b',re.I)
NOTICES={
    '商品已售罄，推荐以下相似商品':'Распродано. Похожие ниже',
}


def select_targets(snapshot,width,height):
    nodes=snapshot.get('nodes',[])
    product_images={}
    for image in nodes[:1200]:
        ib=image.get('box',[])
        if 'ImageView' not in image.get('class','') or len(ib)!=4:continue
        if ib[2]-ib[0]<width*.18 or ib[3]-ib[1]<height*.08:continue
        parent=image.get('parent',-1)
        for _ in range(8):
            if not isinstance(parent,int) or not 0<=parent<len(nodes):break
            product_images.setdefault(parent,[]).append(ib)
            parent=nodes[parent].get('parent',-1)
    selected=[]
    for index,node in enumerate(nodes[:1200]):
        text=' '.join(str(node.get('text','')).split())
        box=node.get('box',[])
        if not text or len(text)>240 or len(box)!=4:continue
        x,y,right,bottom=box
        if not all(isinstance(v,(int,float)) for v in box):continue
        if x<0 or y<0 or right>width or bottom>height or right-x<18 or bottom-y<14:continue
        name=node.get('id','').rsplit('/',1)[-1]
        native_title=name in TITLE_IDS and 'TextView' in node.get('class','')
        if name=='tv_content' and text in NOTICES:
            role='notice';translation=NOTICES[text]
        elif native_title:
            if name=='tv_title' and not node.get('trusted_title'):
                # tv_title is also used by generic dialogs. Require a sizeable
                # product image above this label in the same native container.
                parent=node.get('parent',-1);under_product=False
                for _ in range(6):
                    if not isinstance(parent,int) or not 0<=parent<len(nodes):break
                    for ib in product_images.get(parent,[]):
                        overlap=max(0,min(right,ib[2])-max(x,ib[0]))
                        if overlap>=(right-x)*.6 and ib[3]<=y+(bottom-y)*.3 and y-ib[3]<height*.10:
                            under_product=True
                    if under_product:break
                    parent=nodes[parent].get('parent',-1)
                if not under_product:continue
            if NO_TITLE.search(text) or re.search(r'\d{7,}',text):continue
            role='title';translation=None
        else:
            translation=BUTTONS.get(text.casefold())
            if not translation:continue
            if not any(c in node.get('class','') for c in ('TextView','Button')):continue
            clickable=node.get('clickable',False) or 'Button' in node.get('class','')
            parent=node.get('parent',-1)
            for _ in range(3):
                if not isinstance(parent,int) or not 0<=parent<len(nodes):break
                ancestor=nodes[parent]
                clickable=clickable or ancestor.get('clickable',False)
                ab=ancestor.get('box',[])
                if ancestor.get('clickable',False) and len(ab)==4 and 0<ab[3]-ab[1]<height*.18:
                    # A nav label may use the width of its own button, but never
                    # the neighboring button or the product card/image.
                    x=max(0,ab[0]+4);right=min(width,ab[2]-4)
                    break
                parent=ancestor.get('parent',-1)
            if not clickable:continue
            role='button'
        # Never expand a caption beyond the native label rectangle.
        scroll_box=node.get('scroll_box')
        parent=node.get('parent',-1)
        for _ in range(10):
            if not isinstance(parent,int) or not 0<=parent<len(nodes):break
            cls=nodes[parent].get('class','')
            if any(kind in cls for kind in ('RecyclerView','ScrollView','ProductListView')) and 'Horizontal' not in cls:
                scroll_box=nodes[parent].get('box');break
            parent=nodes[parent].get('parent',-1)
        selected.append({'source':text,'role':role,'translation':translation,
                         'pixels':(int(x),int(y),int(right-x),int(bottom-y)),
                         'box':[x/width,y/height,(right-x)/width,(bottom-y)/height],
                         'scroll_box':scroll_box,
                         'lines':1 if bottom-y<46 else 2})
    # Some widgets duplicate the same label in nested nodes.
    unique={}
    for item in selected:
        key=(item['source'],item['pixels'])
        if key not in unique:unique[key]=item
    return list(unique.values())[:32]
