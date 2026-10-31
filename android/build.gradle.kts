plugins {
    alias(libs.plugins.android.library) apply false
    // AGP 9 compiles Kotlin itself (built-in Kotlin); this only pins the Kotlin Gradle plugin version it uses.
    alias(libs.plugins.kotlin.android) apply false
}
