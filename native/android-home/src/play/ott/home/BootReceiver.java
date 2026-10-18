package play.ott.home;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ResolveInfo;

/** Boot recovery is active only when the user chose OTT Home as default HOME. */
public final class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        if (!Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;
        Intent home = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME);
        ResolveInfo resolved = context.getPackageManager().resolveActivity(home, 0);
        if (resolved == null || resolved.activityInfo == null
            || !context.getPackageName().equals(resolved.activityInfo.packageName)) return;
        context.startActivity(new Intent(context, HomeActivity.class)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
    }
}
