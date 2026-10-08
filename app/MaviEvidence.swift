import Foundation
import CryptoKit

struct MaviArtifact: Codable, Identifiable, Equatable {
    var path: String
    var sha256: String
    var size: UInt64
    var id: String { path }

    init(path: String, sha256: String, size: UInt64) {
        self.path = path
        self.sha256 = sha256
        self.size = size
    }
}

@MainActor enum MaviArtifactEvidence {
    private struct FileSignature: Equatable {
        let size: UInt64
        let modified: TimeInterval
        let inode: UInt64
    }

    private struct Verification: Equatable {
        let signature: FileSignature
        let sha256: String
    }

    private static var verified: [String: Verification] = [:]

    static func beginTask() {
        verified.removeAll(keepingCapacity: true)
    }

    static func capture(_ url: URL) throws -> MaviArtifact {
        let standardized = url.standardizedFileURL
        let signature = try fileSignature(standardized)
        let digest = try hashFile(standardized)
        verified[standardized.path] = Verification(signature: signature, sha256: digest)
        return MaviArtifact(path: standardized.path, sha256: digest, size: signature.size)
    }

    static func matches(_ artifact: MaviArtifact) throws -> Bool {
        let url = URL(fileURLWithPath: artifact.path).standardizedFileURL
        let signature = try fileSignature(url)
        guard signature.size == artifact.size else { return false }
        if let cached = verified[url.path], cached.signature == signature, cached.sha256 == artifact.sha256 { return true }
        let actual = try hashFile(url)
        guard actual == artifact.sha256 else { return false }
        verified[url.path] = Verification(signature: signature, sha256: actual)
        return true
    }

    private static func fileSignature(_ url: URL) throws -> FileSignature {
        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        guard let sizeValue = attributes[.size] as? NSNumber,
              let date = attributes[.modificationDate] as? Date else {
            throw CocoaError(.fileReadNoSuchFile)
        }
        let inodeValue = attributes[.systemFileNumber] as? NSNumber
        return FileSignature(size: sizeValue.uint64Value,
                             modified: date.timeIntervalSince1970,
                             inode: inodeValue?.uint64Value ?? 0)
    }

    private static func hashFile(_ url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1_048_576), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }
}
