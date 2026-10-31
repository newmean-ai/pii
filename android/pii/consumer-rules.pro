# ai.newmean:pii: applied to apps that use the library. The JNI library binds to this class and its native methods by name.
-keep class ai.newmean.pii.internal.NativeBridge {
    native <methods>;
}
