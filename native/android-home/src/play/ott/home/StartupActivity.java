package play.ott.home;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.CompoundButton;
import android.widget.LinearLayout;
import android.widget.Switch;
import android.widget.TextView;

/** Visible opt-in for firmware that does not offer a third-party HOME picker. */
public final class StartupActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        final SharedPreferences preferences = getSharedPreferences(BootReceiver.PREFS, MODE_PRIVATE);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        int padding = (int) (24 * getResources().getDisplayMetrics().density);
        layout.setPadding(padding, padding, padding, padding);
        TextView description = new TextView(this);
        description.setText("Start the installed OTT-play after Android restarts. "
            + "Unlock the tablet normally if required. This does not replace your Home app "
            + "or change OTT profiles and kiosk settings.");
        description.setTextSize(18);
        layout.addView(description);
        final TextView status = new TextView(this);
        String last = preferences.getString(BootReceiver.RESULT, "none");
        status.setText("Last boot launch: " + last + ". Verify OTT and playback after a real restart.");
        Switch enabled = new Switch(this);
        enabled.setText("Start OTT after reboot");
        enabled.setChecked(preferences.getBoolean(BootReceiver.ENABLED, false));
        enabled.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            private boolean restoring;
            @Override public void onCheckedChanged(CompoundButton button, boolean checked) {
                if (restoring) return;
                if (preferences.edit().putBoolean(BootReceiver.ENABLED, checked).commit()) {
                    status.setText(checked ? "Startup enabled. Test with a real restart."
                        : "Startup opt-in disabled. A selected default OTT Home still opens OTT.");
                } else {
                    restoring = true;
                    button.setChecked(!checked);
                    restoring = false;
                    status.setText("Could not save startup setting. Try again.");
                }
            }
        });
        layout.addView(enabled);
        layout.addView(status);
        Button open = new Button(this);
        open.setText("Open OTT-play");
        open.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View view) {
                startActivity(new Intent(StartupActivity.this, HomeActivity.class));
            }
        });
        layout.addView(open);
        Button home = new Button(this);
        home.setText("Android Home settings");
        home.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View view) {
                try { startActivity(new Intent(Settings.ACTION_HOME_SETTINGS)); }
                catch (RuntimeException unavailable) {
                    status.setText("This firmware does not offer Home settings. Use startup opt-in above.");
                }
            }
        });
        layout.addView(home);
        setContentView(layout);
    }
}
