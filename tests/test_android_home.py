"""Run the actual boot receiver against a small Android boundary fixture."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AndroidHomeBootTests(unittest.TestCase):
    def test_receiver_opt_in_default_home_and_blocked_launch(self):
        java_home = os.environ.get("JAVA_HOME")
        suffix = ".exe" if os.name == "nt" else ""
        def executable(name):
            return (str(Path(java_home) / "bin" / (name + suffix))
                    if java_home else shutil.which(name))

        javac, java = executable("javac"), executable("java")
        if not javac or not java:
            self.skipTest("JDK required for Android boot receiver boundary test")
        fixtures = {
            "android/content/BroadcastReceiver.java": """
package android.content;
public abstract class BroadcastReceiver {
    public abstract void onReceive(Context context, Intent intent);
}
""",
            "android/content/Intent.java": """
package android.content;
public class Intent {
    public static final String ACTION_BOOT_COMPLETED="boot", ACTION_MAIN="main", CATEGORY_HOME="home";
    public static final int FLAG_ACTIVITY_NEW_TASK=0x10000000;
    public String action; public Class<?> target; public int flags;
    public Intent(String action) {this.action=action;}
    public Intent(Context context, Class<?> target) {this.target=target;}
    public String getAction() {return action;}
    public Intent addCategory(String category) {return this;}
    public Intent addFlags(int flags) {this.flags|=flags; return this;}
}
""",
            "android/content/SharedPreferences.java": """
package android.content;
public class SharedPreferences {
    public boolean enabled; public String result;
    public boolean getBoolean(String key, boolean fallback) {return enabled;}
    public Editor edit() {return new Editor();}
    public class Editor {
        public Editor putString(String key, String value) {result=value; return this;}
        public void apply() {}
    }
}
""",
            "android/content/Context.java": """
package android.content;
import android.content.pm.PackageManager;
public class Context {
    public static final int MODE_PRIVATE=0;
    public SharedPreferences prefs=new SharedPreferences();
    public PackageManager pm=new PackageManager();
    public int starts; public boolean blocked; public Intent started;
    public SharedPreferences getSharedPreferences(String key, int mode) {return prefs;}
    public PackageManager getPackageManager() {return pm;}
    public String getPackageName() {return "play.ott.home";}
    public void startActivity(Intent intent) {
        if(blocked) throw new SecurityException("blocked by Android");
        starts++; started=intent;
    }
}
""",
            "android/content/pm/ActivityInfo.java": """
package android.content.pm;
public class ActivityInfo {public String packageName;}
""",
            "android/content/pm/ResolveInfo.java": """
package android.content.pm;
public class ResolveInfo {public ActivityInfo activityInfo;}
""",
            "android/content/pm/PackageManager.java": """
package android.content.pm;
import android.content.Intent;
public class PackageManager {
    public ResolveInfo resolved;
    public ResolveInfo resolveActivity(Intent intent, int flags) {return resolved;}
}
""",
            "play/ott/home/HomeActivity.java": """
package play.ott.home;
public class HomeActivity {}
""",
            "play/ott/home/BootTest.java": """
package play.ott.home;
import android.content.*;
import android.content.pm.*;
public class BootTest {
    static void check(boolean value) {if(!value) throw new AssertionError();}
    static Context context(String home, boolean enabled) {
        Context c=new Context(); c.prefs.enabled=enabled;
        if(home!=null) {
            c.pm.resolved=new ResolveInfo(); c.pm.resolved.activityInfo=new ActivityInfo();
            c.pm.resolved.activityInfo.packageName=home;
        }
        return c;
    }
    public static void main(String[] args) {
        BootReceiver receiver=new BootReceiver(); Intent boot=new Intent(Intent.ACTION_BOOT_COMPLETED);
        for(String home:new String[]{null,"android", "com.amazon.firelauncher"}) {
            Context c=context(home,false); receiver.onReceive(c,boot);
            check(c.starts==0 && c.prefs.result==null);
        }
        Context malformed=context(null,false); malformed.pm.resolved=new ResolveInfo();
        receiver.onReceive(malformed,boot); check(malformed.starts==0);
        for(String home:new String[]{null,"android","com.amazon.firelauncher","play.ott.home"}) {
            Context c=context(home,true); receiver.onReceive(c,boot);
            check(c.starts==1 && "requested".equals(c.prefs.result));
            check(c.started.target==HomeActivity.class);
            check((c.started.flags & Intent.FLAG_ACTIVITY_NEW_TASK)!=0);
        }
        Context prior=context("play.ott.home",false); receiver.onReceive(prior,boot);
        check(prior.starts==1); // Existing default-HOME installations remain supported.
        Context ignored=context("play.ott.home",true);
        receiver.onReceive(ignored,new Intent(Intent.ACTION_MAIN)); receiver.onReceive(ignored,null);
        check(ignored.starts==0 && ignored.prefs.result==null);
        Context blocked=context(null,true); blocked.blocked=true;
        receiver.onReceive(blocked,boot); check(blocked.starts==0 && "blocked".equals(blocked.prefs.result));
        Context optedOut=context("com.amazon.firelauncher",true); optedOut.prefs.enabled=false;
        receiver.onReceive(optedOut,boot); check(optedOut.starts==0);
    }
}
""",
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, source in fixtures.items():
                target = directory / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source)
            receiver = directory / "play/ott/home/BootReceiver.java"
            source = ROOT / "native/android-home/src/play/ott/home/BootReceiver.java"
            receiver.write_text(source.read_text())
            sources = [str(path) for path in directory.rglob("*.java")]
            for command in ([javac, "-d", str(directory), *sources],
                            [java, "-cp", str(directory), "play.ott.home.BootTest"]):
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
