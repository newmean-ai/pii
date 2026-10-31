package ai.newmean.pii.internal

import ai.newmean.pii.PiiFilePin
import ai.newmean.pii.PiiModels
import ai.newmean.pii.PiiProgress
import java.io.File
import java.io.FileInputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** Reads, downloads and verifies model files against their pins. */
internal object ModelStore {
    private const val BUFFER = 1 shl 16

    fun mismatch(name: String): IOException =
        IOException("$name does not match the published model file of release ${PiiModels.VERSION} (size or SHA-256 differs)")

    private fun hex(d: ByteArray): String = d.joinToString("") { "%02x".format(it.toInt() and 0xFF) }

    fun sha256(bytes: ByteArray): String = hex(MessageDigest.getInstance("SHA-256").digest(bytes))

    fun matches(bytes: ByteArray, pin: PiiFilePin): Boolean = bytes.size.toLong() == pin.bytes && sha256(bytes) == pin.sha256

    /** All of [input], which must be exactly the pinned file. */
    fun readVerified(input: InputStream, pin: PiiFilePin, name: String): ByteArray {
        val out = ByteArray(pin.bytes.toInt())
        var n = 0
        while (n < out.size) {
            val k = input.read(out, n, out.size - n)
            if (k < 0) throw mismatch(name)
            n += k
        }
        if (input.read() >= 0 || sha256(out) != pin.sha256) throw mismatch(name)
        return out
    }

    fun readVerified(file: File, pin: PiiFilePin): ByteArray {
        if (!file.isFile) throw java.io.FileNotFoundException("$file not found")
        if (file.length() != pin.bytes) throw mismatch(file.path)
        return FileInputStream(file).use { readVerified(it, pin, file.path) }
    }

    private fun isVerified(file: File, pin: PiiFilePin): Boolean {
        if (!file.isFile || file.length() != pin.bytes) return false
        val md = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { input ->
            val buf = ByteArray(BUFFER)
            while (true) {
                val k = input.read(buf)
                if (k < 0) break
                md.update(buf, 0, k)
            }
        }
        return hex(md.digest()) == pin.sha256
    }

    /** [dest] if it already holds the pinned file; otherwise downloads [url] next to it, verifies, and moves it in place. */
    fun fetch(url: String, dest: File, pin: PiiFilePin, onProgress: PiiProgress?): File {
        if (isVerified(dest, pin)) {
            onProgress?.onProgress(dest.name, pin.bytes, pin.bytes, true)
            return dest
        }
        val dir = dest.parentFile ?: throw IOException("no folder for $dest")
        if (!dir.isDirectory && !dir.mkdirs() && !dir.isDirectory) throw IOException("cannot create $dir")
        val tmp = File.createTempFile(dest.name, ".part", dir)
        try {
            val conn = URL(url).openConnection()
            conn.connectTimeout = 30_000
            conn.readTimeout = 60_000
            if (conn is HttpURLConnection) {
                conn.instanceFollowRedirects = true
                val code = conn.responseCode
                if (code != HttpURLConnection.HTTP_OK) {
                    conn.disconnect()
                    throw IOException("failed to download $url: HTTP $code")
                }
            }
            val md = MessageDigest.getInstance("SHA-256")
            var loaded = 0L
            conn.getInputStream().use { input ->
                tmp.outputStream().use { out ->
                    val buf = ByteArray(BUFFER)
                    while (true) {
                        val k = input.read(buf)
                        if (k < 0) break
                        loaded += k
                        if (loaded > pin.bytes) throw mismatch(url)
                        md.update(buf, 0, k)
                        out.write(buf, 0, k)
                        onProgress?.onProgress(dest.name, loaded, pin.bytes, false)
                    }
                }
            }
            if (loaded != pin.bytes || hex(md.digest()) != pin.sha256) throw mismatch(url)
            // another thread or process may have finished the same file first; both copies are identical
            if (!tmp.renameTo(dest) && !isVerified(dest, pin)) {
                dest.delete()
                if (!tmp.renameTo(dest)) throw IOException("cannot move the download to $dest")
            }
            return dest
        } finally {
            tmp.delete()
        }
    }
}
