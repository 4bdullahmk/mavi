import Foundation

enum DiscordChecks {
    static func run() throws {
        let server = "123456789012345678"
        let channel = "234567890123456789"
        let user = "345678901234567890"
        let allowed: Set<String> = [user]
        func parse(_ content: String, author: String? = nil, channelID: String? = nil,
                   guildID: String? = nil, isBot: Bool = false, webhook: String? = nil,
                   attachments: [DiscordRemoteAttachment] = []) -> (command: String, text: String)? {
            DiscordRequestPolicy.parse(content: content, authorID: author ?? user, allowedUserIDs: allowed,
                channelID: channelID ?? channel, expectedChannelID: channel, guildID: guildID,
                expectedGuildID: server, isBot: isBot, webhookID: webhook, attachments: attachments)
        }

        assert(DiscordRequestPolicy.validSnowflake(server))
        assert(!DiscordRequestPolicy.validSnowflake("abc"))
        assert(!DiscordRequestPolicy.validSnowflake("1234567890123456"))
        assert(DiscordRequestPolicy.snowflakeLess("99999999999999999", "100000000000000000"))
        assert(DiscordRequestPolicy.isAfterCursor("345678901234567891", cursor: "345678901234567890"))
        assert(!DiscordRequestPolicy.isAfterCursor("345678901234567889", cursor: "345678901234567890"))
        assert(parse("!mavi ask Summarize the latest task")?.command == "ask")
        assert(parse("!mavi stop")?.command == "stop")
        assert(parse("!mavi status")?.command == "status")
        assert(parse("!mavi help")?.command == "help")
        assert(parse("!mavi answer I approve")?.text == "I approve")
        assert(parse("!mavi task do the work")?.command == "task")
        assert(parse("!mavi ask") == nil)
        assert(parse("!mavi stop now") == nil)
        assert(parse("!mavi status details") == nil)
        assert(parse("!mavi ask private", author: "456789012345678901") == nil)
        assert(parse("!mavi ask bot", isBot: true) == nil)
        assert(parse("!mavi ask webhook", webhook: "webhook") == nil)
        assert(parse("!mavi ask wrong server", guildID: "456789012345678901") == nil)
        assert(parse("!mavi ask wrong channel", channelID: "456789012345678901") == nil)
        assert(parse("!mavi ask \(String(repeating: "x", count: 4000))") == nil)
        assert(parse("prefix !mavi ask ignored") == nil)

        let image = DiscordRemoteAttachment(id: "1", url: "https://cdn.discordapp.com/image.png",
            filename: "image.png", size: 100, contentType: "image/png")
        assert(parse("!mavi ask describe this", attachments: [image])?.command == "ask")
        assert(parse("!mavi task inspect", attachments: [image])?.command == "task")
        assert(parse("!mavi help", attachments: [image]) == nil)
        assert(parse("!mavi ask", attachments: [image]) == nil)
        let tooLarge = DiscordRemoteAttachment(id: "2", url: "https://cdn.discordapp.com/large.bin",
            filename: "large.bin", size: 20 * 1024 * 1024 + 1, contentType: nil)
        assert(parse("!mavi ask inspect", attachments: [tooLarge]) == nil)
        let six = Array(repeating: image, count: 6)
        let seven = Array(repeating: image, count: 7)
        assert(parse("!mavi ask inspect", attachments: six)?.command == "ask")
        assert(parse("!mavi ask inspect", attachments: seven) == nil)
    }
}
