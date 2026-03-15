const API_URL = "";

const fileInput = document.getElementById('fileInput');
const fileNameDisplay = document.getElementById('fileNameDisplay');
const uploadForm = document.getElementById('uploadForm');
const downloadForm = document.getElementById('downloadForm');
const termLog = document.getElementById('termLog');

const resultBox = document.getElementById('uploadResult');
const resultToken = document.getElementById('resultToken');
const resultKey = document.getElementById('resultKey');

// Helpers
function logToTerminal(msg, isError = false) {
    const div = document.createElement('div');
    const timestamp = new Date().toISOString().split('T')[1].slice(0, 8);
    div.textContent = `[${timestamp}] > ${msg}`;
    if (isError) {
        div.classList.add('text-red');
    }
    termLog.appendChild(div);
    termLog.scrollTop = termLog.scrollHeight;
}

fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        fileNameDisplay.textContent = e.target.files[0].name;
        logToTerminal(`PAYLOAD SELECTED: ${e.target.files[0].name} (${e.target.files[0].size} bytes)`);
    } else {
        fileNameDisplay.textContent = "CHOOSE_FILE";
    }
});

// ---------- Encryption Helpers ---------- //
// format: b"AV1" + nonce(12) + ct
const MAGIC_BYTES = new TextEncoder().encode("AV1");

function buf2b64(buffer) {
    let binary = '';
    const bytes = new Uint8Array(buffer);
    for (let i = 0; i < bytes.byteLength; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    return window.btoa(binary);
}

function b642buf(base64) {
    const binary_string = window.atob(base64);
    const len = binary_string.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
        bytes[i] = binary_string.charCodeAt(i);
    }
    return bytes.buffer;
}

// Generate a random 32-byte AES key
async function generateKey() {
    logToTerminal("GENERATING 256-BIT SECURE CRYPTO KEY...");
    const rawKey = new Uint8Array(32);
    window.crypto.getRandomValues(rawKey);
    logToTerminal("KEY GENERATED.");
    return rawKey;
}

// Encrypt payload matching Python backend's crypto.py
async function encryptPayload(keyBytes, payloadBuffer) {
    logToTerminal("IMPORTING KEY TO WEB CRYPTO API...");
    const key = await window.crypto.subtle.importKey(
        "raw",
        keyBytes,
        "AES-GCM",
        true,
        ["encrypt", "decrypt"]
    );

    logToTerminal("GENERATING 12-BYTE NONCE/IV...");
    const nonce = new Uint8Array(12);
    window.crypto.getRandomValues(nonce);

    logToTerminal("ENCRYPTING PAYLOAD (AES-GCM)...");
    const ciphertext = await window.crypto.subtle.encrypt(
        {
            name: "AES-GCM",
            iv: nonce,
        },
        key,
        payloadBuffer
    );

    logToTerminal("PACKAGING ENVELOPE (PREFIX + IV + CT)...");
    const envelope = new Uint8Array(MAGIC_BYTES.length + nonce.length + ciphertext.byteLength);
    envelope.set(MAGIC_BYTES, 0);
    envelope.set(nonce, MAGIC_BYTES.length);
    envelope.set(new Uint8Array(ciphertext), MAGIC_BYTES.length + nonce.length);

    return envelope;
}

// Decrypt matching Python backend
async function decryptPayload(keyBytes, blobBuffer) {
    const blobArray = new Uint8Array(blobBuffer);
    const prefixLen = MAGIC_BYTES.length;

    // verify prefix
    for (let i = 0; i < prefixLen; i++) {
        if (blobArray[i] !== MAGIC_BYTES[i]) {
            throw new Error("Invalid prefix - not an AV1 envelope");
        }
    }
    logToTerminal("MAGIC BYTES (AV1) VERIFIED");

    const nonce = blobArray.slice(prefixLen, prefixLen + 12);
    const ciphertext = blobArray.slice(prefixLen + 12);

    logToTerminal("IMPORTING KEY...");
    const key = await window.crypto.subtle.importKey(
        "raw",
        keyBytes,
        "AES-GCM",
        true,
        ["decrypt"]
    );

    logToTerminal("DECRYPTING PAYLOAD...");
    const plaintext = await window.crypto.subtle.decrypt(
        {
            name: "AES-GCM",
            iv: nonce,
        },
        key,
        ciphertext
    );
    return plaintext;
}


// ---------- Upload Flow ---------- //
uploadForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const file = fileInput.files[0];
    const ttl = document.getElementById('ttlInput').value;

    if (!file) {
        logToTerminal("ERROR: NO PAYLOAD SELECTED", true);
        return;
    }

    termLog.innerHTML = "";
    logToTerminal(`INITIATING UPLOAD PROTOCOL FOR '${file.name}'`);

    try {
        const fileBuffer = await file.arrayBuffer();

        const rawKey = await generateKey();
        const keyB64 = buf2b64(rawKey);

        const envelope = await encryptPayload(rawKey, fileBuffer);

        logToTerminal("PREPARING MULTIPART FORM...");
        const formData = new FormData();
        // convert to Blob so we can send as File
        const encryptedBlob = new Blob([envelope], { type: 'application/octet-stream' });
        formData.append("file", encryptedBlob, file.name);

        logToTerminal(`TRANSMITTING TO CLASSIFIED NETWORK (TTL=${ttl}s)...`);

        const response = await fetch(`${API_URL}/secrets?ttl=${ttl}`, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const errData = await response.json();
            throw new Error(errData.detail || `HTTP ${response.status}`);
        }

        const data = await response.json();

        logToTerminal("TRANSMISSION COMPLETE");
        logToTerminal(`PAYLOAD SECURED WITH TOKEN ID: ${data.token}`);

        resultToken.value = data.token;
        resultKey.value = keyB64;
        resultBox.classList.remove('hidden');

        uploadForm.reset();
        fileNameDisplay.textContent = "CHOOSE_FILE";

    } catch (err) {
        logToTerminal(`UPLOAD FAILED: ${err.message}`, true);
    }
});


// ---------- Download Flow ---------- //
downloadForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const token = document.getElementById('tokenInput').value.trim();
    const keyB64 = document.getElementById('keyInput').value.trim();

    if (!token || !keyB64) return;

    termLog.innerHTML = "";
    logToTerminal(`INITIATING EXTRACTION PROTOCOL FOR TOKEN: ${token}`);

    try {
        // Decode key
        let rawKey;
        try {
            const buf = b642buf(keyB64);
            rawKey = new Uint8Array(buf);
            if (rawKey.length !== 32) {
                throw new Error("Key length is not 32 bytes");
            }
            logToTerminal("DECRYPTION KEY PARSED SUCCESSFULLY");
        } catch (e) {
            throw new Error(`Invalid Base64 key: ${e.message}`);
        }

        logToTerminal("REQUESTING DATA FROM SECURE VAULT...");
        const response = await fetch(`${API_URL}/secrets/${token}`);

        if (!response.ok) {
            const errorData = await response.json().catch(() => null);
            const det = errorData ? errorData.detail : "Unknown storage error";
            throw new Error(`[${response.status}] ${det}`);
        }

        logToTerminal("PAYLOAD ACQUIRED. INITIATING BURN_AFTER_READ_DESTRUCTION.");

        const encryptedBuffer = await response.arrayBuffer();

        const plainBuffer = await decryptPayload(rawKey, encryptedBuffer);

        logToTerminal("PAYLOAD DECRYPTED SUCCESSFULLY");

        // Trigger download
        logToTerminal("PACKAGING FOR LOCAL FILESYSTEM EXFILTRATION...");
        const downloadBlob = new Blob([plainBuffer], { type: 'application/octet-stream' });
        const url = window.URL.createObjectURL(downloadBlob);

        const a = document.createElement("a");
        a.href = url;
        a.download = `extracted_payload_${token.substring(0, 6)}.bin`; // Ideally we'd capture original filename, but it's not exposed cleanly in current spec
        document.body.appendChild(a);
        a.click();

        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        logToTerminal("EXTRACTION COMPLETE");
        document.getElementById('tokenInput').value = "";
        document.getElementById('keyInput').value = "";

    } catch (err) {
        logToTerminal(`EXTRACTION FAILED: ${err.message}`, true);
    }
});
