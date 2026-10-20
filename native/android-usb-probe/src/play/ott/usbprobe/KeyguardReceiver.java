package play.ott.usbprobe;

import android.app.KeyguardManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import org.json.JSONObject;

/** Read-only keyguard state for the authorized ADB shell; performs no input. */
public final class KeyguardReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        if (!"play.ott.usbprobe.KEYGUARD".equals(intent.getAction())) return;
        try {
            KeyguardManager manager = (KeyguardManager) context.getSystemService(Context.KEYGUARD_SERVICE);
            if (manager == null) throw new IllegalStateException("Keyguard unavailable");
            JSONObject state = new JSONObject();
            state.put("secure", manager.isKeyguardSecure());
            state.put("locked", manager.isKeyguardLocked());
            setResultCode(0);
            setResultData(state.toString());
        } catch (Exception unavailable) {
            setResultCode(1);
            setResultData("unavailable");
        }
    }
}
