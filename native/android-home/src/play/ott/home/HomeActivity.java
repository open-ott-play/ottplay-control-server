package play.ott.home;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ComponentName;
import android.content.Intent;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/** A HOME entry point only: the official OTT APK owns playback and settings. */
public final class HomeActivity extends Activity {
    private TextView status;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setGravity(Gravity.CENTER);
        int padding = (int) (24 * getResources().getDisplayMetrics().density);
        layout.setPadding(padding, padding, padding, padding);
        status = new TextView(this);
        status.setTextSize(20);
        status.setGravity(Gravity.CENTER);
        status.setText("Opening OTT-play…");
        layout.addView(status);
        Button retry = new Button(this);
        retry.setText("Open OTT-play");
        retry.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View view) { launchPlayer(); }
        });
        layout.addView(retry);
        setContentView(layout);
    }

    @Override protected void onResume() {
        super.onResume();
        // Android resumes HOME on each Home press or when the player task exits.
        // No timer runs in the background and no other activity is intercepted.
        launchPlayer();
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent); // Android follows this with onResume, including singleTask reuse.
    }

    private void launchPlayer() {
        Intent player = new Intent(Intent.ACTION_MAIN)
            .addCategory(Intent.CATEGORY_LAUNCHER)
            .setComponent(new ComponentName("play.ott.foss", "play.ott.foss.MainActivity"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
        try {
            startActivity(player);
            status.setText("Opening OTT-play…");
        } catch (ActivityNotFoundException missing) {
            status.setText("OTT-play is not installed or is disabled.\n"
                + "Install or enable the official play.ott.foss APK, then tap Open OTT-play.");
        } catch (SecurityException denied) {
            status.setText("Android blocked opening OTT-play.\n"
                + "Check the installed application and device policy, then try again.");
        }
    }
}
