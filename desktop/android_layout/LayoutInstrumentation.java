package local.pinduo.layout;

import android.app.Instrumentation;
import android.app.UiAutomation;
import android.accessibilityservice.AccessibilityServiceInfo;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicLong;
import org.json.*;

/** Read-only layout feed for our dedicated emulator. Never injects input. */
public final class LayoutInstrumentation extends Instrumentation {
    private final AtomicLong revision = new AtomicLong(1);
    private final AtomicLong lastScrollAt = new AtomicLong(0);
    private UiAutomation automation;
    private static String str(CharSequence value) { return value == null ? "" : value.toString(); }
    @Override public void onCreate(Bundle args) { super.onCreate(args); start(); }
    @Override public void onStart() {
        try {
            automation = getUiAutomation(UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES);
            AccessibilityServiceInfo info = automation.getServiceInfo();
            info.flags |= AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS;
            automation.setServiceInfo(info);
            automation.setOnAccessibilityEventListener(event -> {
                int type = event.getEventType();
                if(type==AccessibilityEvent.TYPE_VIEW_SCROLLED) lastScrollAt.set(SystemClock.uptimeMillis());
                if (type == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED ||
                    type == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED ||
                    type == AccessibilityEvent.TYPE_VIEW_SCROLLED ||
                    type == AccessibilityEvent.TYPE_WINDOWS_CHANGED) revision.incrementAndGet();
            });
            try (ServerSocket server = new ServerSocket()) {
                server.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"),18894));
                while (true) {
                    try (Socket socket = server.accept()) {
                        socket.setSoTimeout(0);
                        BufferedReader input = new BufferedReader(new InputStreamReader(socket.getInputStream(),StandardCharsets.UTF_8));
                        BufferedWriter output = new BufferedWriter(new OutputStreamWriter(socket.getOutputStream(),StandardCharsets.UTF_8));
                        String line;
                        while ((line=input.readLine())!=null) {
                            boolean forceFast=line.startsWith("fast ");
                            if (!line.startsWith("layout") && !forceFast) break;
                            long known=-1;
                            try { int offset=forceFast?5:7;if(line.length()>offset)known=Long.parseLong(line.substring(offset)); } catch(NumberFormatException ignored) {}
                            JSONObject value;
                            try {
                                if(forceFast || SystemClock.uptimeMillis()-lastScrollAt.get()<200) {
                                    value=snapshot(true);value.put("moving",true);value.put("fast",true);
                                } else if(known==revision.get()) {
                                    value=new JSONObject();value.put("revision",known);value.put("unchanged",true);
                                } else value=snapshot(false);
                            } catch(Exception transientFailure) {
                                value=new JSONObject();value.put("nodes",new JSONArray());
                                value.put("revision",revision.incrementAndGet());value.put("retry",true);
                            }
                            output.write(value.toString()); output.write('\n'); output.flush();
                        }
                    } catch (IOException ignored) { }
                }
            }
        } catch (Exception ignored) { finish(1,new Bundle()); }
    }
    private JSONObject snapshot(boolean fast) throws JSONException {
        JSONObject result = new JSONObject();
        result.put("revision",revision.get());
        JSONArray nodes = new JSONArray(); result.put("nodes",nodes);
        automation.clearCache();
        AccessibilityNodeInfo root = automation.getRootInActiveWindow();
        if (root == null) return result;
        result.put("window",root.getWindowId());
        String pkg = str(root.getPackageName());
        // Do not export Google, system settings, keyboards or other applications.
        if (!pkg.equals("com.xunmeng.pinduoduo") && !pkg.equals("local.pinduo.fixture")) return result;
        result.put("package",pkg);
        if(fast) {
            String[] names={"tv_title","goods_name","goods_title","tv_goods_name","tv_goods_title","product_title"};
            for(String name:names) {
                for(AccessibilityNodeInfo title:root.findAccessibilityNodeInfosByViewId(pkg+":id/"+name)) {
                    Rect scroll=scrollBounds(title);
                    if(!title.isVisibleToUser() || title.isPassword() || title.isEditable() || (scroll==null && name.equals("tv_title"))) {title.recycle();continue;}
                    Rect bounds=new Rect();title.getBoundsInScreen(bounds);
                    JSONObject item=new JSONObject();item.put("parent",-1);item.put("id",pkg+":id/"+name);
                    item.put("class",str(title.getClassName()));item.put("text",str(title.getText()));item.put("trusted_title",true);
                    item.put("box",new JSONArray(new int[]{bounds.left,bounds.top,bounds.right,bounds.bottom}));
                    if(scroll!=null)item.put("scroll_box",new JSONArray(new int[]{scroll.left,scroll.top,scroll.right,scroll.bottom}));
                    nodes.put(item);title.recycle();
                }
            }
            root.recycle();return result;
        }
        walk(root,nodes,-1,0);
        return result;
    }
    private Rect scrollBounds(AccessibilityNodeInfo node) {
        AccessibilityNodeInfo parent=node.getParent();
        for(int i=0;i<9 && parent!=null;i++) {
            String name=str(parent.getClassName());
            if((name.contains("RecyclerView") || name.contains("ScrollView") || name.contains("ProductListView")) && !name.contains("Horizontal")) {
                Rect rect=new Rect();parent.getBoundsInScreen(rect);parent.recycle();return rect;
            }
            AccessibilityNodeInfo next=parent.getParent();parent.recycle();parent=next;
        }
        if(parent!=null)parent.recycle();return null;
    }
    private void walk(AccessibilityNodeInfo node,JSONArray nodes,int parent,int depth) throws JSONException {
        if (node==null || nodes.length()>=1200 || depth>40) return;
        if (!node.isVisibleToUser()) { node.recycle(); return; }
        Rect bounds=new Rect(); node.getBoundsInScreen(bounds);
        boolean privateField=node.isPassword() || node.isEditable() || str(node.getClassName()).contains("EditText");
        JSONObject entry=new JSONObject();
        int index=nodes.length();
        entry.put("parent",parent); entry.put("id",str(node.getViewIdResourceName()));
        entry.put("class",str(node.getClassName())); entry.put("clickable",node.isClickable());
        entry.put("box",new JSONArray(new int[]{bounds.left,bounds.top,bounds.right,bounds.bottom}));
        // Only explicit product title nodes and generic action labels are needed.
        // Editable/password text and content descriptions are never exported.
        String text=privateField ? "" : str(node.getText());
        entry.put("text",text.length()>240 ? text.substring(0,240) : text);
        nodes.put(entry);
        for (int i=0;i<node.getChildCount();i++) walk(node.getChild(i),nodes,index,depth+1);
        node.recycle();
    }
}
