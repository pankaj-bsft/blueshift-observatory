# frozen_string_literal: true

require "json"
require "active_record"

require "/Users/pankaj/pani/git/bsft-models/lib/bsft-models"

def db_config
  {
    adapter: "mysql2",
    host: ENV["BSFT_DB_HOST"],
    database: ENV["BSFT_DB_NAME"],
    username: ENV["BSFT_DB_USER"],
    password: ENV["BSFT_DB_PASSWORD"],
    port: (ENV["BSFT_DB_PORT"] || "3306").to_i
  }
end

def adapter_ids
  raw = ENV["BSFT_ADAPTER_IDS"]
  return [] if raw.nil? || raw.strip.empty?
  raw.split(",").map(&:strip).map(&:to_i).reject(&:zero?)
end

def extract_domain(from_address)
  return nil if from_address.nil?
  addr = from_address.to_s.strip.downcase
  return nil if addr.empty?
  return addr.split("@", 2)[1] if addr.include?("@")
  addr
end

ActiveRecord::Base.establish_connection(db_config)
Bsft::Models.configure do |c|
  c.connection = ActiveRecord::Base.connection
end

ids = adapter_ids
if ids.empty?
  STDERR.puts "BSFT_ADAPTER_IDS must be set (e.g., \"8,9,10\")"
  exit 1
end

records = Bsft::Models::AccountAdapter.where(adapter_id: ids).includes(account: :billing_account)

mappings = []
records.find_each do |aa|
  domain = extract_domain(aa.read_attribute(:from_address))
  next if domain.nil? || domain.empty?

  account = aa.account
  account_name = if account&.respond_to?(:billing_account) && account.billing_account
                   account.billing_account.name
                 else
                   account&.name
                 end

  next if account_name.nil? || account_name.to_s.strip.empty?

  mappings << {
    id: aa.id,
    sending_domain: domain,
    account_name: account_name,
    is_affiliate: false,
    notes: "",
    created_at: aa.created_at
  }
end

puts JSON.generate({ mappings: mappings })
