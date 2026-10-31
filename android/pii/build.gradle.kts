import java.io.ByteArrayOutputStream
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.library)
    `maven-publish`
    signing
}

group = "ai.newmean"
version = "1.0.0"

// The native core lives in the repository's native/core and is compiled from there (never copied).
val coreDir = rootProject.layout.projectDirectory.dir("../native/core")
// Fixtures and local model copies of the npm package's tests (private; tests skip without them). Env overrides.
val repoJsTest = rootProject.layout.projectDirectory.dir("../js/test")

android {
    namespace = "ai.newmean.pii"
    compileSdk = 36
    ndkVersion = "29.0.14206865"

    defaultConfig {
        minSdk = 24
        consumerProguardFiles("consumer-rules.pro")
        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64")
        }
        externalNativeBuild {
            cmake {
                // C only: no C++ runtime in the AAR
                arguments += listOf("-DANDROID_STL=none", "-DPII_CORE_DIR=${coreDir.asFile.absolutePath}")
                targets += "newmean_pii_jni"
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            externalNativeBuild {
                cmake {
                    // the core's -O3 applies to the Release configuration
                    arguments += "-DCMAKE_BUILD_TYPE=Release"
                }
            }
        }
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }

    publishing {
        singleVariant("release") {
            withSourcesJar()
            withJavadocJar() // Maven Central requires one (AGP builds it with Dokka)
        }
    }
}

kotlin {
    explicitApi()
    compilerOptions {
        jvmTarget = JvmTarget.JVM_17
    }
}

// LICENSE and NOTICE travel in the AAR (classes.jar META-INF/ai.newmean.pii/), as Apache-2.0 asks of redistributions. A
// folder of their own: AGP's packaging excludes drop META-INF/LICENSE and META-INF/NOTICE, and no other library uses it.
abstract class LegalFiles : DefaultTask() {
    @get:InputFiles abstract val files: ConfigurableFileCollection
    @get:OutputDirectory abstract val outputDir: DirectoryProperty

    @TaskAction
    fun copy() {
        val dir = outputDir.get().asFile.apply { deleteRecursively() }.resolve("META-INF/ai.newmean.pii").apply { mkdirs() }
        files.forEach { it.copyTo(File(dir, it.name), overwrite = true) }
    }
}

val legalFiles = tasks.register<LegalFiles>("legalFiles") {
    files.from(rootProject.file("LICENSE"), rootProject.file("NOTICE"))
}

androidComponents.onVariants { variant ->
    variant.sources.resources?.addGeneratedSourceDirectory(legalFiles, LegalFiles::outputDir)
}

dependencies {
    implementation(libs.kotlinx.coroutines.core)
    testImplementation(libs.junit)
    testImplementation(libs.json)
}

// ---- JNI library for the host JVM (macOS, Linux), so unit tests run the real model without an emulator.
// Same CMakeLists.txt as the AAR; needs cmake and a C compiler. -Ppii.hostJni=false skips it (the parity tests then skip).
val hostJniEnabled = providers.gradleProperty("pii.hostJni").map { it != "false" }.getOrElse(true) &&
    !System.getProperty("os.name").startsWith("Windows")
val hostJniDir = layout.buildDirectory.dir("host-jni")
val hostJniLib = hostJniDir.map { it.file(if (System.getProperty("os.name").startsWith("Mac")) "libnewmean_pii_jni.dylib" else "libnewmean_pii_jni.so") }

fun findCmake(): String {
    val sdk = System.getenv("ANDROID_HOME") ?: System.getenv("ANDROID_SDK_ROOT")
    val path = System.getenv("PATH").orEmpty().split(File.pathSeparator).map { File(it, "cmake") }
    val inSdk = sdk?.let { File(it, "cmake").listFiles()?.sortedDescending()?.map { d -> File(d, "bin/cmake") } }.orEmpty()
    return (path + inSdk).firstOrNull { it.canExecute() }?.absolutePath ?: "cmake"
}

// JNI headers: a JDK's include/ (with its platform subfolder), else the NDK's jni.h, which is self-contained and describes
// the same standard JNI ABI (the Android Studio JBR ships no headers).
fun jniInclude(): File {
    val jdks = listOfNotNull(System.getProperty("java.home"), System.getenv("JAVA_HOME")).map { File(it, "include") }
    jdks.firstOrNull { File(it, "jni.h").isFile }?.let { return it }
    val ndkJni = androidComponents.sdkComponents.ndkDirectory.get().asFile.resolve("toolchains/llvm/prebuilt").listFiles().orEmpty()
        .map { File(it, "sysroot/usr/include/jni.h") }.firstOrNull { it.isFile }
        ?: throw GradleException("no jni.h: set JAVA_HOME to a JDK with include/, or install NDK ${android.ndkVersion}")
    val dir = hostJniDir.get().dir("jni-include").asFile
    dir.mkdirs()
    ndkJni.copyTo(File(dir, "jni.h"), overwrite = true)
    return dir
}

val configureHostJni = tasks.register<Exec>("configureHostJni") {
    inputs.file("src/main/cpp/CMakeLists.txt")
    outputs.file(hostJniDir.map { it.file("CMakeCache.txt") })
    doFirst {
        commandLine(
            findCmake(), "-S", file("src/main/cpp").absolutePath, "-B", hostJniDir.get().asFile.absolutePath,
            "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_LIBRARY_OUTPUT_DIRECTORY=${hostJniDir.get().asFile.absolutePath}",
            "-DPII_CORE_DIR=${coreDir.asFile.absolutePath}", "-DPII_JNI_INCLUDE=${jniInclude().absolutePath}",
        )
    }
    standardOutput = ByteArrayOutputStream()
}

val buildHostJni = tasks.register<Exec>("buildHostJni") {
    dependsOn(configureHostJni)
    inputs.dir("src/main/cpp")
    inputs.dir(coreDir.dir("src"))
    inputs.file(coreDir.file("pii.h"))
    outputs.file(hostJniLib)
    commandLine(findCmake(), "--build", hostJniDir.get().asFile.absolutePath, "--target", "newmean_pii_jni", "--parallel")
    standardOutput = ByteArrayOutputStream()
}

tasks.withType<Test>().configureEach {
    if (hostJniEnabled) {
        dependsOn(buildHostJni)
        systemProperty("ai.newmean.pii.library", hostJniLib.get().asFile.absolutePath)
    }
    systemProperty("pii.fixtures", System.getenv("PII_FIXTURES") ?: repoJsTest.dir("fixtures").asFile.absolutePath)
    systemProperty("pii.models", System.getenv("PII_MODELS") ?: repoJsTest.dir("models").asFile.absolutePath)
    systemProperty("pii.jsModels", rootProject.layout.projectDirectory.file("../js/src/models.ts").asFile.absolutePath)
    listOf("PII_SIZES", "PII_DOWNLOAD", "PII_NPM_SPANS").forEach { k -> System.getenv(k)?.let { systemProperty("pii.env.$k", it) } }
    maxHeapSize = "2g"
    testLogging {
        events("passed", "skipped", "failed")
        showStandardStreams = true
        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
    }
}

publishing {
    publications {
        register<MavenPublication>("release") {
            groupId = "ai.newmean"
            artifactId = "pii"
            version = project.version.toString()
            afterEvaluate { from(components["release"]) }
            pom {
                name = "newmean-pii"
                description = "On-device multilingual PII detection and masking with the newmean-pii models (Android)."
                url = "https://github.com/newmean-ai/pii"
                licenses {
                    license {
                        name = "Apache-2.0"
                        url = "https://www.apache.org/licenses/LICENSE-2.0"
                    }
                }
                developers {
                    developer {
                        id = "newmean-ai"
                        name = "newmean.ai"
                        url = "https://newmean.ai"
                    }
                }
                scm {
                    url = "https://github.com/newmean-ai/pii"
                    connection = "scm:git:https://github.com/newmean-ai/pii.git"
                }
            }
        }
    }
    repositories {
        // ./gradlew :pii:publishReleasePublicationToLocalRepository writes pii/build/repo (for checking the POM and AAR)
        maven {
            name = "local"
            url = uri(layout.buildDirectory.dir("repo"))
        }
    }
}

// Maven Central wants a .asc signature next to every file. The release workflow passes the ASCII-armored key and its
// passphrase as SIGNING_KEY and SIGNING_PASSWORD; without them nothing is signed (local builds and CI checks).
val signingKey = providers.environmentVariable("SIGNING_KEY")
signing {
    isRequired = signingKey.isPresent
    if (signingKey.isPresent) {
        useInMemoryPgpKeys(signingKey.get(), providers.environmentVariable("SIGNING_PASSWORD").orNull)
        sign(publishing.publications["release"])
    }
}
