package play.ott.home;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ResolveInfo;

/** Start only after explicit startup opt-in or selection as the default HOME. */
public final class BootReceiver extends BroadcastReceiver {
    static final String PREFS = "startup";
    static final String ENABLED = "boot_enabled";
    static final String RESULT = "last_boot_result";

    @Override public void onReceive(Context context, Intent intent) {
        if (intent == null || !Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;
        SharedPreferences preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        Intent home = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME);
        ResolveInfo resolved = context.getPackageManager().resolveActivity(home, 0);
        boolean defaultHome = resolved != null && resolved.activityInfo != null
            && context.getPackageName().equals(resolved.activityInfo.packageName);
        if (!preferences.getBoolean(ENABLED, false) && !defaultHome) return;
        String result;
        try {
            context.startActivity(new Intent(context, HomeActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            // Dispatch is not proof that Android displayed OTT or resumed playback.
            result = "requested";
        } catch (RuntimeException blocked) {
            result = "blocked";
        }
        preferences.edit().putString(RESULT, result).apply();
    }
}
